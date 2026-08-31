from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Annotated

import typer

from .audit import audit_dataset
from .blade3d import build_blade
from .fitting_v2 import fit_anchors_to_dataset
from .geomturbo import export_geomturbo
from .legacy import ingest_legacy_anchors, legacy_section_schedule
from .models import SectionSchedule
from .sampling import generate_dataset
from .sampling_v2 import generate_dataset_v2
from .storage import DatasetStore
from .storage_v2 import DatasetStoreV2
from .workspace import WorkspaceStore

app = typer.Typer(help="Cascade Forge 叶型几何数据库")
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PACKAGE_ROOT / "configs" / "geometry-v1.0.0.yaml"
DEFAULT_CONFIG_V2 = PACKAGE_ROOT / "configs" / "geometry-v2.0.0.yaml"


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
        if dataset_version == "2.0.0" and config.resolve() == DEFAULT_CONFIG.resolve()
        else config
    )
    if dataset_version == "2.0.0" or "2.0.0" in selected_config.name:
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
    summary = audit_dataset(dataset)
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
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    import uvicorn

    from .web import create_app

    uvicorn.run(
        create_app(dataset, v2_dataset_root=v2_dataset, workspace_root=workspace),
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


def _open_store(dataset: Path):
    manifest = dataset / "dataset_manifest.json"
    if manifest.exists():
        schema = json.loads(manifest.read_text(encoding="utf-8")).get("schema_version")
        if str(schema) == "2.0.0":
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
