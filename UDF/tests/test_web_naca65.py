from __future__ import annotations

import time

from fastapi.testclient import TestClient

from cascade_forge.models_naca65 import NACA65BenchmarkConfig, NACA65Spec
from cascade_forge.web import create_app


def _wait(client: TestClient, job_id: str) -> dict:
    for _ in range(300):
        response = client.get(f"/api/v2/jobs/{job_id}")
        if response.status_code == 200 and response.json()["status"] in {
            "completed",
            "failed",
        }:
            return response.json()
        time.sleep(0.02)
    raise AssertionError(f"job timeout: {job_id}")


def test_naca65_generate_fit_benchmark_and_exports(tmp_path) -> None:
    benchmark_root = tmp_path / "naca65"
    with TestClient(
        create_app(
            tmp_path / "v1",
            v2_dataset_root=tmp_path / "v2",
            naca65_dataset_root=benchmark_root,
        )
    ) as client:
        page = client.get("/naca65")
        assert page.status_code == 200
        assert "NACA 65 实验室" in page.text
        assert "naca65.js" in page.text
        assert "法向分解校验" in page.text
        assert "data-naca-normals" in page.text
        assert "data-invariant=\"midpoint_error_max\"" in page.text

        parsed = client.post(
            "/api/v2/naca65/presets/parse", json={"designation": "65-(12)10"}
        )
        assert parsed.status_code == 200
        assert parsed.json()["spec"]["design_lift_coefficient"] == 1.2
        assert parsed.json()["spec"]["thickness_ratio"] == 0.1

        spec = NACA65Spec().model_dump(mode="json")
        generated = client.post("/api/v2/naca65/generate", json=spec)
        assert generated.status_code == 200
        assert len(generated.json()["reference"]["raw_suction"]) == 176

        fitted = client.post(
            "/api/v2/naca65/fit",
            json={
                "spec": spec,
                "max_outer_iterations": 1,
                "max_design_evaluations": 10,
            },
        )
        assert fitted.status_code == 200
        assert fitted.json()["fit_report"]["geometry_id"].startswith("geo_naca_normal_")
        assert fitted.json()["fit_report"]["diagnostics"]["midpoint_error_max"] < 1e-10
        assert fitted.json()["fit_report"]["diagnostics"]["normal_offset_error_max"] < 1e-10
        assert fitted.json()["fitted_geometry"]["trailing_osculating_circle"] == []

        config = NACA65BenchmarkConfig(
            thickness_ratios=(0.10,),
            design_lift_coefficients=(0.8,),
            series_65_loading_parameters=(0.8,),
            max_outer_iterations=1,
            max_design_evaluations=10,
        )
        started = client.post(
            "/api/v2/naca65/benchmarks", json=config.model_dump(mode="json")
        )
        assert started.status_code == 202
        job = _wait(client, started.json()["job_id"])
        assert job["status"] == "completed", job
        assert job["result"]["completed_cases"] == 2

        cases = client.get("/api/v2/naca65/cases").json()
        assert cases["total"] == 2
        fit_id = cases["items"][0]["fit_id"]
        assert client.get(f"/api/v2/naca65/cases/{fit_id}").status_code == 200
        for output_format in ("csv", "json", "parquet"):
            response = client.get(
                f"/api/v2/naca65/cases/{fit_id}/export",
                params={"format": output_format},
            )
            assert response.status_code == 200
            assert response.content

        manifest = client.get("/api/v2/naca65/manifest")
        assert manifest.status_code == 200
        assert manifest.json()["total_cases"] == 2
        assert manifest.json()["benchmark_version"] == "naca65-benchmark-3.0.0"
        jobs = client.get("/api/v2/jobs").json()["items"]
        assert any(item["job_id"] == job["job_id"] for item in jobs)
