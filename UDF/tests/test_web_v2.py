from __future__ import annotations

import time

from fastapi.testclient import TestClient

from cascade_forge.features import extract_features
from cascade_forge.geometry2d import generate_profile
from cascade_forge.models import SectionDefinition, SectionSchedule
from cascade_forge.models_v2 import GeometryIntentV2, GeometrySpecV2
from cascade_forge.storage_v2 import DatasetStoreV2
from cascade_forge.validation import validate_profile
from cascade_forge.web import create_app


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


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(300):
        job = client.get(f"/api/v2/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job timeout: {job_id}")


def test_v2_preview_database_compare_cascade_and_workspace(tmp_path) -> None:
    v1_root = tmp_path / "v1"
    v2_root = tmp_path / "v2"
    workspace = tmp_path / "workspace"
    geometry = _seed_v2(v2_root)
    with TestClient(
        create_app(v1_root, v2_dataset_root=v2_root, workspace_root=workspace)
    ) as client:
        spec = GeometrySpecV2(intent=geometry.spec.intent)
        preview = client.post("/api/v2/preview", json=spec.model_dump(mode="json"))
        assert preview.status_code == 200
        payload = preview.json()
        assert payload["geometry"]["leading_osculating_circle"]
        assert payload["geometry"]["trailing_osculating_circle"]
        assert payload["features"]["kernel"] == "unified_bspline_g2"
        resolved = client.post("/api/v2/curves/resolve", json=spec.model_dump(mode="json"))
        assert resolved.status_code == 200
        assert resolved.json()["spec"]["camber_curve"] is not None

        listing = client.get(
            "/api/v2/geometries",
            params={"origin": "generated", "status": "warning"},
        ).json()
        assert listing["total"] == 1
        assert listing["items"][0]["geometry_id"] == geometry.geometry_id
        assert client.get(f"/api/v2/geometries/{geometry.geometry_id}").status_code == 200
        exported_profile = client.get(f"/api/v2/geometries/{geometry.geometry_id}/export")
        assert exported_profile.status_code == 200
        assert exported_profile.text.startswith("side,point,x_over_c,y_over_c")
        comparison = client.post(
            "/api/v2/compare",
            json={"geometry_ids": [geometry.geometry_id, geometry.geometry_id]},
        )
        assert comparison.status_code == 200
        cascade = client.post(
            "/api/v2/cascade",
            json={"geometry_id": geometry.geometry_id, "solidity": 1.2},
        ).json()
        assert len(cascade["passages"]) == 3

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
        assert fits.status_code == 200
        assert fits.json()["total"] == 0

        studio = client.get("/studio")
        assert studio.status_code == 200
        assert "密切圆半径" in studio.text
        assert "Bezier" not in studio.text


def test_v2_blade_job_and_artifact_download(tmp_path) -> None:
    v1_root = tmp_path / "v1"
    v2_root = tmp_path / "v2"
    geometry = _seed_v2(v2_root)
    schedule = SectionSchedule(
        name="api-blade",
        blade_count=18,
        sections=[
            SectionDefinition(
                span_fraction=0.0,
                radius_m=0.2,
                chord_m=0.04,
                geometry_id=geometry.geometry_id,
                stagger_deg=30.0,
            ),
            SectionDefinition(
                span_fraction=1.0,
                radius_m=0.3,
                chord_m=0.03,
                geometry_id=geometry.geometry_id,
                stagger_deg=25.0,
            ),
        ],
    )
    with TestClient(create_app(v1_root, v2_dataset_root=v2_root)) as client:
        preview = client.post("/api/v2/blades/preview", json=schedule.model_dump(mode="json"))
        assert preview.status_code == 200
        assert len(preview.json()["sections"]) == 2
        response = client.post("/api/v2/blades", json=schedule.model_dump(mode="json"))
        assert response.status_code == 202
        job = _wait(client, response.json()["job_id"])
        assert job["status"] == "completed", job
        artifact = job["result"]["artifact"]
        downloaded = client.get(f"/api/v2/artifacts/{artifact}")
        assert downloaded.status_code == 200
        assert downloaded.text.startswith("GEOMETRY TURBO VERSION 3.7")
