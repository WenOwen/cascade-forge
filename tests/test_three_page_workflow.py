import re

import pytest
from fastapi.testclient import TestClient

from cascade_forge.api.app import create_app
from cascade_forge.domain.circular_arc import default_double_arc_spec


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "dataset", profile_database_root=tmp_path / "databases")
    ) as client:
        yield client


@pytest.mark.parametrize(
    "path",
    ["/naca65", "/arc", "/compare", "/cascade", "/blade", "/static/naca65.js", "/static/arc.js"],
)
def test_specialist_pages_and_assets_are_removed(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/naca65/generate",
        "/api/v2/naca65/fit",
        "/api/v2/naca65/presets/parse",
        "/api/v2/naca65/export/dat",
        "/api/v2/naca65/benchmarks",
        "/api/v2/arc/generate",
        "/api/v2/compare",
        "/api/v2/cascade",
        "/api/v2/blades",
        "/api/v2/blades/preview",
    ],
)
def test_specialist_post_routes_are_removed(client, path):
    assert client.post(path, json={}).status_code == 404


def test_only_three_pages_in_navigation_and_no_specialist_openapi(client):
    for path in ["/studio", "/standard", "/database"]:
        response = client.get(path)
        assert response.status_code == 200
        assert len(re.findall(r'class="nav-item', response.text)) == 3
    paths = client.app.openapi()["paths"]
    for prefix in ["/api/v2/naca65", "/api/v2/blades", "/api/v2/artifacts"]:
        assert not any(path.startswith(prefix) for path in paths)
    assert "/api/v2/arc/limits" in paths
    assert "/api/workbench/geomturbo" in paths
    assert "/api/v2/jobs/{job_id}" in paths
    assert "/api/v2/compare" not in paths and "/api/v2/cascade" not in paths


def test_standard_limits_and_generation_remain_available(client):
    spec = default_double_arc_spec().model_dump(mode="json")
    limits = client.post("/api/v2/arc/limits", json=spec)
    assert limits.status_code == 200
    assert limits.json()["geometry_feasible"]
    generated = client.post("/api/workbench/preview", json={"family": "double_arc", "spec": spec})
    assert generated.status_code == 200
    assert generated.json()["family"] == "double_arc"


def test_startup_leaves_existing_naca_benchmark_data_untouched(tmp_path):
    benchmark = tmp_path / "datasets/naca65-compressor-benchmark-v1.0.0"
    benchmark.mkdir(parents=True)
    marker = benchmark / "existing-user-data.bin"
    marker.write_bytes(b"preserve this benchmark")
    with TestClient(
        create_app(
            tmp_path / "datasets/geometry-v2.1.0", profile_database_root=tmp_path / "databases"
        )
    ) as client:
        assert client.get("/health").status_code == 200
        assert not hasattr(client.app.state, "naca65_store")
    assert marker.read_bytes() == b"preserve this benchmark"
    assert list(benchmark.iterdir()) == [marker]
