from __future__ import annotations

import json

from cascade_forge.features import extract_features
from cascade_forge.geometry2d import generate_profile
from cascade_forge.models_v2 import GeometryIntentV2, GeometrySpecV2
from cascade_forge.storage_v2 import DatasetStoreV2
from cascade_forge.validation import validate_profile
from cascade_forge.workspace import WorkspaceStore


def _geometry():
    return generate_profile(
        GeometrySpecV2(
            intent=GeometryIntentV2(
                inlet_metal_angle_deg=55.0,
                outlet_metal_angle_deg=30.0,
                stagger_deg=42.0,
                max_thickness_ratio=0.1,
                max_thickness_x_ratio=0.42,
                leading_edge_radius_ratio=0.008,
                trailing_edge_radius_ratio=0.002,
            )
        )
    )


def test_v2_store_round_trip_and_filters(tmp_path) -> None:
    store = DatasetStoreV2(tmp_path / "dataset")
    geometry = _geometry()
    features = extract_features(geometry)
    report = validate_profile(geometry)
    store.append_geometries(
        [(geometry, features, report, {"proposal_index": 2})],
        batch_metadata={"accepted": 1},
    )
    assert store.count("generated") == 1
    assert store.geometry_ids() == {geometry.geometry_id}
    rebuilt = store.get_geometry(geometry.geometry_id)
    assert rebuilt is not None
    assert rebuilt.model_dump(mode="json") == geometry.model_dump(mode="json")
    rows = store.list_geometries(status=report.status.value, origin="generated")
    assert rows[0]["intent_id"] == geometry.intent_id
    assert rows[0]["kernel"] == "unified_bspline_g2"
    batch = json.loads(next(store.batch_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert batch["row_count"] == 1


def test_workspace_is_revisioned_and_separate(tmp_path) -> None:
    workspace = WorkspaceStore(tmp_path / "workspace")
    geometry = _geometry()
    first = workspace.save_design("方案 A", geometry, tags=["manual"])
    second = workspace.save_design("方案 A", geometry)
    assert first != second
    rows = workspace.list_designs()
    assert {row["revision"] for row in rows} == {1, 2}
    record = workspace.get_design(first)
    assert record is not None
    assert record["geometry"].origin == "workspace"
    destination = tmp_path / "design.json"
    workspace.export_design(first, destination)
    imported = workspace.import_design(destination)
    assert workspace.get_design(imported)["geometry"].geometry_id == geometry.geometry_id
