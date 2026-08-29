from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Annotated

import typer

from .audit import audit_dataset
from .blade3d import build_blade
from .geomturbo import export_geomturbo
from .legacy import ingest_legacy_anchors, legacy_section_schedule
from .models import SectionSchedule
from .sampling import generate_dataset
from .storage import DatasetStore

app = typer.Typer(help="Cascade Forge 叶型几何数据库")
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PACKAGE_ROOT / "configs" / "geometry-v1.0.0.yaml"


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
) -> None:
    def progress(state: dict[str, int]) -> None:
        typer.echo(
            f"proposals={state['proposals']} accepted={state['accepted']} "
            f"rejected={state['rejected']}"
        )

    manifest = generate_dataset(config, output, target_valid=target_valid, progress=progress)
    typer.echo(manifest.model_dump_json(indent=2))


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
    geometry = DatasetStore(dataset).get_geometry(geometry_id)
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
    store = DatasetStore(dataset)
    blade = build_blade(schedule_model, store.get_geometry)
    path = export_geomturbo(blade, destination)
    typer.echo(str(path))


@app.command("serve")
def serve_command(
    dataset: Annotated[Path, typer.Option()],
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(dataset), host=host, port=port)


if __name__ == "__main__":
    app()
