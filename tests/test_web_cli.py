import pytest
from fastapi.testclient import TestClient

from cascade_forge.api.app import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "dataset", profile_database_root=tmp_path / "databases")
    ) as client:
        yield client


def test_offline_assets_and_retained_pages(client):
    for path in (
        "/",
        "/studio",
        "/standard",
        "/database",
        "/health",
        "/assets/plotly.min.js",
        "/static/app.js",
    ):
        assert client.get(path).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/anchors",
        "/datasets",
        "/audit",
        "/jobs",
        "/api/v1/geometries",
        "/api/v2/anchor-fits",
        "/api/v2/audit",
        "/api/v2/jobs",
    ],
)
def test_removed_get_routes(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize(
    "path", ["/api/v1/preview", "/api/v1/batches", "/api/v2/batches", "/api/v2/anchor-fits"]
)
def test_removed_post_routes(client, path):
    assert client.post(path, json={}).status_code == 404
