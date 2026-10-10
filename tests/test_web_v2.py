from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient

from cascade_forge.api.app import create_app
from cascade_forge.domain.features import extract_features
from cascade_forge.domain.geometry import generate_profile
from cascade_forge.domain.models import (
    GeometryIntentV2,
    GeometrySpecV2,
)
from cascade_forge.domain.validation import validate_profile
from cascade_forge.storage.dataset import DatasetStoreV2


def _seed_v2(path):
    geometry = generate_profile(
        GeometrySpecV2(
            intent=GeometryIntentV2(
                inlet_metal_angle_deg=55.0,
                outlet_metal_angle_deg=30.0,
                max_thickness_ratio=0.1,
                max_thickness_x_ratio=0.42,
                leading_edge_radius_ratio=0.008,
                trailing_edge_radius_ratio=0.002,
            )
        )
    )
    DatasetStoreV2(path).append_geometries(
        [
            (
                geometry,
                extract_features(geometry),
                validate_profile(geometry),
                {"proposal_index": 0},
            )
        ]
    )
    return geometry


def test_v2_preview_database_compare_cascade_and_workspace(tmp_path) -> None:
    v2_root = tmp_path / "v2"
    workspace = tmp_path / "workspace"
    geometry = _seed_v2(v2_root)
    with TestClient(
        create_app(v2_root, workspace_root=workspace, profile_database_root=tmp_path / "databases")
    ) as client:
        spec = GeometrySpecV2(intent=geometry.spec.intent)
        preview = client.post("/api/v2/preview", json=spec.model_dump(mode="json"))
        assert preview.status_code == 200
        payload = preview.json()
        assert payload["geometry"]["leading_osculating_circle"]
        assert payload["geometry"]["trailing_osculating_circle"]
        assert payload["placement"]["coordinate_frame"] == "x-axial,y-positive-tangential"
        assert payload["placement"]["stagger_deg"] == 42.0
        assert payload["placement"]["control_points"]
        assert payload["features"]["kernel"] == "unified_bspline_g2"
        assert payload["features"]["metal_angle_difference_deg"] == 25.0

        moved_intent = geometry.spec.intent.model_copy(
            update={
                "stagger_deg": 50.0,
                "inlet_metal_angle_deg": 63.0,
                "outlet_metal_angle_deg": 38.0,
            }
        )
        moved = client.post(
            "/api/v2/preview",
            json=GeometrySpecV2(intent=moved_intent).model_dump(mode="json"),
        ).json()
        assert moved["geometry"]["geometry_id"] == payload["geometry"]["geometry_id"]
        assert moved["geometry"]["intent_id"] == payload["geometry"]["intent_id"]
        np.testing.assert_array_equal(moved["geometry"]["suction"], payload["geometry"]["suction"])
        delta = np.radians(8.0)
        rotation = np.array([[np.cos(delta), -np.sin(delta)], [np.sin(delta), np.cos(delta)]])
        np.testing.assert_allclose(
            moved["placement"]["suction"],
            np.asarray(payload["placement"]["suction"]) @ rotation.T,
            atol=1e-12,
        )
        resolved = client.post("/api/v2/curves/resolve", json=spec.model_dump(mode="json"))
        assert resolved.status_code == 200
        assert resolved.json()["spec"]["camber_curve"] is not None

        listing = client.get(
            "/api/v2/geometries",
            params={"origin": "generated", "status": "valid"},
        ).json()
        assert listing["total"] == 1
        assert listing["items"][0]["geometry_id"] == geometry.geometry_id
        assert client.get(f"/api/v2/geometries/{geometry.geometry_id}").status_code == 200
        exported_profile = client.get(f"/api/v2/geometries/{geometry.geometry_id}/export")
        assert exported_profile.status_code == 200
        assert exported_profile.text.startswith("side,point,x_over_c,y_over_c")
        saved = client.post(
            "/api/v2/workspace/designs",
            json={"name": "测试设计", "tags": ["manual"], "spec": spec.model_dump(mode="json")},
        )
        assert saved.status_code == 201
        design_id = saved.json()["design_id"]
        assert client.get(f"/api/v2/workspace/designs/{design_id}").status_code == 200
        exported = client.get(f"/api/v2/workspace/designs/{design_id}/export")
        assert exported.status_code == 200
        imported = client.post("/api/v2/workspace/import", json=exported.json())
        assert imported.status_code == 201
        fits = client.get("/api/v2/anchor-fits")
        assert fits.status_code == 404

        studio = client.get("/studio")
        assert studio.status_code == 200
        assert "密切圆半径" in studio.text
        assert "Bezier" not in studio.text
        assert "高级" not in studio.text
        assert "切向（ANSYS）" in studio.text
        assert "弯转角" not in studio.text
        assert "data-control-overlay" not in studio.text
