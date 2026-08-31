from __future__ import annotations

import json

from typer.testing import CliRunner

from cascade_forge.cli import app
from cascade_forge.geometry2d import generate_profile
from cascade_forge.models_v2 import GeometryIntentV2, GeometrySpecV2
from cascade_forge.storage_v2 import DatasetStoreV2
from cascade_forge.workspace import WorkspaceStore


def test_cli_v2_generate_export_and_audit_dispatch(tmp_path) -> None:
    runner = CliRunner()
    dataset = tmp_path / "v2"
    generated = runner.invoke(
        app,
        [
            "generate",
            "--output",
            str(dataset),
            "--dataset-version",
            "2.0.0",
            "--target-valid",
            "2",
            "--workers",
            "1",
        ],
    )
    assert generated.exit_code == 0, generated.stdout
    manifest = json.loads((dataset / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "2.0.0"
    assert manifest["generated_valid"] == 2

    geometry_id = next(iter(DatasetStoreV2(dataset).geometry_ids()))
    destination = tmp_path / "profile.csv"
    exported = runner.invoke(
        app,
        [
            "export",
            geometry_id,
            "--dataset",
            str(dataset),
            "--destination",
            str(destination),
        ],
    )
    assert exported.exit_code == 0, exported.stdout
    assert destination.read_text(encoding="utf-8").startswith("side,point,x_over_c,y_over_c")


def test_cli_workspace_import_export(tmp_path) -> None:
    runner = CliRunner()
    source_workspace = tmp_path / "source-workspace"
    target_workspace = tmp_path / "target-workspace"
    geometry = generate_profile(
        GeometrySpecV2(
            intent=GeometryIntentV2(
                inlet_metal_angle_deg=55,
                turning_angle_deg=25,
                max_thickness_ratio=0.1,
                max_thickness_x_ratio=0.42,
                leading_edge_radius_ratio=0.008,
                trailing_edge_radius_ratio=0.002,
            )
        )
    )
    design_id = WorkspaceStore(source_workspace).save_design("CLI 设计", geometry)
    exported_path = tmp_path / "design.json"
    exported = runner.invoke(
        app,
        [
            "workspace-export",
            design_id,
            "--workspace",
            str(source_workspace),
            "--destination",
            str(exported_path),
        ],
    )
    assert exported.exit_code == 0, exported.stdout
    imported = runner.invoke(
        app,
        [
            "workspace-import",
            "--source",
            str(exported_path),
            "--workspace",
            str(target_workspace),
        ],
    )
    assert imported.exit_code == 0, imported.stdout
    assert len(WorkspaceStore(target_workspace).list_designs()) == 1
