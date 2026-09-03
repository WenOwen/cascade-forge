from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Annotated

import typer

from .audit import audit_dataset
from .audit_v2 import audit_dataset_v2
from .benchmark_naca65 import run_naca65_benchmark
from .blade3d import build_blade
from .fitting_naca65 import fit_reference_geometry
from .fitting_v2 import fit_anchors_to_dataset
from .geomturbo import export_geomturbo
from .legacy import ingest_legacy_anchors, legacy_section_schedule
from .models import SectionSchedule
from .models_naca65 import NACA65BenchmarkConfig, NACA65Family, NACA65Spec
from .naca65 import generate_naca65, parse_naca65_designation
from .sampling import generate_dataset
from .sampling_v2 import generate_dataset_v2
from .storage import DatasetStore
from .storage_naca65 import NACA65BenchmarkStore
from .storage_v2 import DatasetStoreV2
from .workspace import WorkspaceStore

app = typer.Typer(help="Cascade Forge 叶型几何数据库")
naca65_app = typer.Typer(help="NACA 65/65A 原生生成与 B-spline 拟合")
app.add_typer(naca65_app, name="naca65")
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PACKAGE_ROOT / "configs" / "geometry-v1.0.0.yaml"
DEFAULT_CONFIG_V2 = PACKAGE_ROOT / "configs" / "geometry-v2.1.0.yaml"


@app.command("ingest-anchors")
def ingest_anchors_command(
    source: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    dataset: Annotated[Path, typer.Option()],
    raw_destination: Annotated[Path | None, typer.Option()] = None,
) -> None:
    manifest = ingest_legacy_anchors(source, dataset, raw_destination=raw_destination)
    typer.echo(
        json.dumps(
            {"count": manifest["count"], "dataset": str(dataset)},
            ensure_ascii=False,
        )
    )


@app.command("generate")
def generate_command(
    output: Annotated[Path, typer.Option()],
    config: Annotated[Path, typer.Option(exists=True)] = DEFAULT_CONFIG,
    target_valid: Annotated[int | None, typer.Option(min=1)] = None,
    dataset_version: Annotated[str, typer.Option()] = "1.0.0",
    workers: Annotated[int | None, typer.Option(min=1, max=64)] = None,
) -> None:
    def progress(state: dict[str, int]) -> None:
        typer.echo(
            f"proposals={state['proposals']} accepted={state['accepted']} "
            f"rejected={state['rejected']}"
        )

    selected_config = (
        DEFAULT_CONFIG_V2
        if dataset_version == "2.1.0" and config.resolve() == DEFAULT_CONFIG.resolve()
        else config
    )
    if dataset_version == "2.1.0" or "2.1.0" in selected_config.name:
        manifest = generate_dataset_v2(
            selected_config,
            output,
            target_valid=target_valid,
            workers=workers,
            progress=progress,
        )
    else:
        manifest = generate_dataset(
            selected_config, output, target_valid=target_valid, progress=progress
        )
    typer.echo(manifest.model_dump_json(indent=2))


@app.command("fit-anchors")
def fit_anchors_command(
    dataset: Annotated[Path, typer.Option()],
    source: Annotated[Path, typer.Option(exists=True, file_okay=False)] = (
        PACKAGE_ROOT / "data" / "raw" / "nasa120859"
    ),
) -> None:
    def progress(state: dict[str, int]) -> None:
        typer.echo(f"completed={state['completed']}/{state['total']}")

    result = fit_anchors_to_dataset(source, dataset, progress=progress)
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("audit")
def audit_command(dataset: Annotated[Path, typer.Option(exists=True, file_okay=False)]) -> None:
    summary = (
        audit_dataset_v2(dataset)
        if isinstance(_open_store(dataset), DatasetStoreV2)
        else audit_dataset(dataset)
    )
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["valid"]:
        raise typer.Exit(code=1)


@app.command("export")
def export_command(
    geometry_id: str,
    dataset: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    destination: Annotated[Path, typer.Option()],
) -> None:
    geometry = _open_store(dataset).get_geometry(geometry_id)
    if geometry is None:
        raise typer.BadParameter(f"找不到 {geometry_id}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    suction, pressure, _, _ = geometry.arrays()
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["side", "point", "x_over_c", "y_over_c"])
        for side, coordinates in (("suction", suction), ("pressure", pressure)):
            for index, coordinate in enumerate(coordinates):
                writer.writerow([side, index, *coordinate])
    typer.echo(str(destination.resolve()))


@app.command("build-blade")
def build_blade_command(
    dataset: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    destination: Annotated[Path, typer.Option()],
    schedule: Annotated[Path | None, typer.Option(exists=True)] = None,
    legacy_row: Annotated[str | None, typer.Option()] = None,
    raw_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = (
        PACKAGE_ROOT / "data" / "raw" / "nasa120859"
    ),
) -> None:
    if schedule is None and legacy_row is None:
        raise typer.BadParameter("必须提供 --schedule 或 --legacy-row")
    if schedule is not None:
        schedule_model = SectionSchedule.model_validate_json(schedule.read_text(encoding="utf-8"))
    else:
        schedule_model = legacy_section_schedule(
            raw_root, legacy_row or "", output_section_count=14
        )
    store = _open_store(dataset)
    blade = build_blade(schedule_model, store.get_geometry)
    path = export_geomturbo(blade, destination)
    typer.echo(str(path))


@app.command("serve")
def serve_command(
    dataset: Annotated[Path, typer.Option()],
    v2_dataset: Annotated[Path | None, typer.Option()] = None,
    workspace: Annotated[Path | None, typer.Option()] = None,
    naca65_dataset: Annotated[Path | None, typer.Option()] = None,
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    import uvicorn

    from .web import create_app

    uvicorn.run(
        create_app(
            dataset,
            v2_dataset_root=v2_dataset,
            workspace_root=workspace,
            naca65_dataset_root=naca65_dataset,
        ),
        host=host,
        port=port,
    )


@app.command("workspace-list")
def workspace_list_command(
    workspace: Annotated[Path, typer.Option()],
) -> None:
    rows = WorkspaceStore(workspace).list_designs(limit=1000)
    typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))


@app.command("workspace-export")
def workspace_export_command(
    design_id: str,
    workspace: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    destination: Annotated[Path, typer.Option()],
) -> None:
    path = WorkspaceStore(workspace).export_design(design_id, destination)
    typer.echo(str(path))


@app.command("workspace-import")
def workspace_import_command(
    source: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    workspace: Annotated[Path, typer.Option()],
) -> None:
    design_id = WorkspaceStore(workspace).import_design(source)
    typer.echo(design_id)


@naca65_app.command("generate")
def naca65_generate_command(
    family: Annotated[str, typer.Option()] = "65",
    thickness_ratio: Annotated[float, typer.Option(min=0.03, max=0.18)] = 0.10,
    design_lift_coefficient: Annotated[float, typer.Option(min=0.0, max=2.0)] = 0.8,
    loading_parameter_a: Annotated[float, typer.Option(min=0.0, max=1.0)] = 0.8,
    preset: Annotated[str | None, typer.Option()] = None,
    destination: Annotated[Path | None, typer.Option()] = None,
) -> None:
    spec = _naca65_spec(
        family,
        thickness_ratio,
        design_lift_coefficient,
        loading_parameter_a,
        preset,
    )
    geometry = generate_naca65(spec)
    payload = geometry.model_dump_json(indent=2)
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
        typer.echo(str(destination.resolve()))
    else:
        typer.echo(payload)


@naca65_app.command("fit")
def naca65_fit_command(
    family: Annotated[str, typer.Option()] = "65",
    thickness_ratio: Annotated[float, typer.Option(min=0.03, max=0.18)] = 0.10,
    design_lift_coefficient: Annotated[float, typer.Option(min=0.0, max=2.0)] = 0.8,
    loading_parameter_a: Annotated[float, typer.Option(min=0.0, max=1.0)] = 0.8,
    preset: Annotated[str | None, typer.Option()] = None,
    destination: Annotated[Path | None, typer.Option()] = None,
    max_outer_iterations: Annotated[int, typer.Option(min=1, max=20)] = 6,
    max_design_evaluations: Annotated[int, typer.Option(min=10, max=500)] = 80,
) -> None:
    reference = generate_naca65(
        _naca65_spec(
            family,
            thickness_ratio,
            design_lift_coefficient,
            loading_parameter_a,
            preset,
        )
    )
    report, fitted = fit_reference_geometry(
        reference,
        max_outer_iterations=max_outer_iterations,
        max_design_evaluations=max_design_evaluations,
    )
    payload = json.dumps(
        {
            "reference": reference.model_dump(mode="json"),
            "fit_report": report.model_dump(mode="json"),
            "fitted_geometry": fitted.model_dump(mode="json"),
        },
        indent=2,
        ensure_ascii=False,
    )
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
        typer.echo(str(destination.resolve()))
    else:
        typer.echo(payload)


@naca65_app.command("benchmark")
def naca65_benchmark_command(
    output: Annotated[Path, typer.Option()],
    max_outer_iterations: Annotated[int, typer.Option(min=1, max=20)] = 6,
    max_design_evaluations: Annotated[int, typer.Option(min=10, max=500)] = 80,
) -> None:
    config = NACA65BenchmarkConfig(
        max_outer_iterations=max_outer_iterations,
        max_design_evaluations=max_design_evaluations,
    )

    def progress(state: dict[str, int]) -> None:
        typer.echo(f"completed={state['completed']}/{state['total']} stored={state['stored']}")

    manifest = run_naca65_benchmark(config, output, progress=progress)
    typer.echo(manifest.model_dump_json(indent=2))


@naca65_app.command("export")
def naca65_export_command(
    fit_id: str,
    dataset: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    destination: Annotated[Path, typer.Option()],
) -> None:
    case = NACA65BenchmarkStore(dataset).get_case(fit_id)
    if case is None:
        raise typer.BadParameter(f"找不到 {fit_id}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix.lower()
    if suffix == ".json":
        destination.write_text(
            json.dumps(case, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        rows = _naca65_coordinate_rows(case)
        if suffix == ".parquet":
            import pyarrow as pa
            import pyarrow.parquet as pq

            pq.write_table(pa.Table.from_pylist(rows), destination, compression="zstd")
        elif suffix == ".csv":
            with destination.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        else:
            raise typer.BadParameter("destination 扩展名必须为 .csv、.json 或 .parquet")
    typer.echo(str(destination.resolve()))


def _naca65_spec(
    family: str,
    thickness_ratio: float,
    design_lift_coefficient: float,
    loading_parameter_a: float,
    preset: str | None,
) -> NACA65Spec:
    if preset:
        return parse_naca65_designation(preset)
    try:
        selected_family = NACA65Family(family.upper())
    except ValueError as exc:
        raise typer.BadParameter("family 仅支持 65 或 65A") from exc
    if selected_family == NACA65Family.SERIES_65A:
        loading_parameter_a = 0.8
    return NACA65Spec(
        family=selected_family,
        thickness_ratio=thickness_ratio,
        design_lift_coefficient=design_lift_coefficient,
        loading_parameter_a=loading_parameter_a,
    )


def _naca65_coordinate_rows(case: dict) -> list[dict]:
    reference = case["reference"]
    fitted = case.get("fitted_geometry")
    sources = [
        ("native", "suction", reference["normalized_suction"]),
        ("native", "pressure", reference["normalized_pressure"]),
    ]
    if fitted:
        sources.extend(
            [
                ("bspline_fit", "suction", fitted["suction"]),
                ("bspline_fit", "pressure", fitted["pressure"]),
            ]
        )
    return [
        {
            "source": source,
            "side": side,
            "point": index,
            "x_over_c": point[0],
            "y_over_c": point[1],
        }
        for source, side, coordinates in sources
        for index, point in enumerate(coordinates)
    ]


def _open_store(dataset: Path):
    if dataset.name.startswith("geometry-v2."):
        return DatasetStoreV2(dataset)
    manifest = dataset / "dataset_manifest.json"
    if manifest.exists():
        schema = json.loads(manifest.read_text(encoding="utf-8")).get("schema_version")
        if str(schema) == "2.1.0":
            return DatasetStoreV2(dataset)
    if (dataset / "tables" / "geometries").exists() and any(
        (dataset / "tables" / "geometries").glob("*.parquet")
    ):
        import pyarrow.parquet as pq

        first = next((dataset / "tables" / "geometries").glob("*.parquet"))
        if "intent_id" in pq.read_schema(first).names:
            return DatasetStoreV2(dataset)
    return DatasetStore(dataset)


if __name__ == "__main__":
    app()
