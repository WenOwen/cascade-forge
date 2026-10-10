from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cascade_forge.cli import app
from cascade_forge.features import extract_features
from cascade_forge.geometry2d import generate_profile
from cascade_forge.models import GeometrySpec
from cascade_forge.storage import DatasetStore
from cascade_forge.validation import validate_profile
from cascade_forge.web import create_app


def _seed_dataset(path: Path):
    spec = GeometrySpec(
        inlet_metal_angle_deg=58,
        turning_angle_deg=32,
        quarter_angle_bias_deg=0,
        three_quarter_angle_bias_deg=0,
        max_thickness_ratio=0.10,
        max_thickness_x_ratio=0.40,
        leading_edge_radius_ratio=0.004,
        trailing_edge_thickness_ratio=0.008,
        trailing_edge_half_wedge_deg=8,
        fore_shape_factor=1,
        aft_shape_factor=1,
    )
    geometry = generate_profile(spec)
    DatasetStore(path).append_geometries(
        [(geometry, extract_features(geometry), validate_profile(geometry))]
    )
    return geometry


def test_web_api_and_offline_assets(tmp_path: Path) -> None:
    geometry = _seed_dataset(tmp_path / "dataset")
    with TestClient(create_app(tmp_path / "dataset")) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/").status_code == 200
        assert client.get("/assets/plotly.min.js").status_code == 200
        assert client.get("/static/htmx.min.js").status_code == 200
        listing = client.get("/api/v1/geometries").json()
        assert listing["total"] == 1
        assert listing["items"][0]["geometry_id"] == geometry.geometry_id
        detail = client.get(f"/api/v1/geometries/{geometry.geometry_id}")
        assert detail.status_code == 200
        preview = client.post("/api/v1/preview", json=geometry.spec.model_dump(mode="json"))
        assert preview.status_code == 200
        assert preview.json()["geometry"]["geometry_id"] == geometry.geometry_id
        form = geometry.spec.model_dump(mode="json")
        for key in ["handedness", "points_per_side", "generator_version", "coordinate_convention"]:
            form.pop(key)
        assert client.post("/ui/preview", data=form).status_code == 200
        comparison = client.get(
            "/ui/compare", params={"left": geometry.geometry_id, "right": geometry.geometry_id}
        )
        assert comparison.status_code == 200


def test_background_batch_job_and_cli_audit(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    with TestClient(create_app(dataset)) as client:
        response = client.post("/api/v1/batches", json={"target_valid": 2})
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert job["status"] == "completed", job

    runner = CliRunner()
    result = runner.invoke(app, ["audit", "--dataset", str(dataset)])
    assert result.exit_code == 0
    assert '"generated": 2' in result.stdout
