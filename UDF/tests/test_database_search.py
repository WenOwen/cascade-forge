from fastapi.testclient import TestClient
from test_web_v2 import _seed_v2

from cascade_forge.web import create_app


def test_database_filter_totals_search_and_pagination(tmp_path):
    geometry = _seed_v2(tmp_path / "v2")
    with TestClient(create_app(tmp_path / "v1", v2_dataset_root=tmp_path / "v2")) as client:
        for filters, expected in [
            ({}, 1),
            ({"status": "valid"}, 0),
            ({"origin": "anchor_fit"}, 0),
            ({"search": geometry.geometry_id.upper()[5:18]}, 1),
            ({"search": "  " + geometry.geometry_id + "  "}, 1),
            ({"search": "%"}, 0),
            ({"search": "' OR 1=1 --"}, 0),
            ({"search": geometry.geometry_id, "origin": "anchor_fit"}, 0),
        ]:
            response = client.get("/api/v2/geometries", params=filters)
            assert response.status_code == 200
            data = response.json()
            assert data["total"] == expected
            assert len(data["items"]) == expected
        page = client.get("/api/v2/geometries", params={"offset": 1, "limit": 1}).json()
        assert page["total"] == 1
        assert page["items"] == []
        assert client.get("/api/v2/geometries?sort=invalid").status_code == 422
        assert client.get("/api/v2/geometries", params={"search": "x" * 121}).status_code == 422


def test_empty_database_rejects_invalid_sort(tmp_path):
    with TestClient(create_app(tmp_path / "v1")) as client:
        assert client.get("/api/v2/geometries?sort=invalid").status_code == 422
        assert client.get("/api/v2/geometries").json()["total"] == 0
