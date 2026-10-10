import ast
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from test_profile_workbench import request_body
from test_web_v2 import _seed_v2

from cascade_forge.api.app import create_app
from cascade_forge.storage.profile_library import ProfileLibrary
from cascade_forge.workbench.generator import generate_record


@pytest.fixture
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "dataset", profile_database_root=tmp_path / "databases")
    ) as client:
        yield client


@pytest.mark.parametrize("family", ["custom", "naca65", "double_arc"])
def test_delete_is_persisted_and_preserves_files(client, family):
    body = request_body(family)
    preview = client.post("/api/workbench/preview", json=body).json()
    assert preview["label"] and preview["label"] != preview["geometry_id"]
    saved = client.post("/api/workbench/profiles", json=body).json()
    identifier = saved["geometry_id"]
    files = {path: path.read_bytes() for path in Path(saved["path"]).glob(identifier + ".*")}
    assert files
    assert client.delete("/api/workbench/profiles/" + identifier).status_code == 200
    assert client.get("/api/workbench/profiles/" + identifier).status_code == 404
    assert client.get("/api/workbench/profiles").json()["total"] == 0
    assert ProfileLibrary(Path(saved["path"])).get(identifier) is None
    for path, data in files.items():
        assert path.read_bytes() == data
    archive = ZipFile(BytesIO(client.get("/api/workbench/dataset/export").content))
    assert json.loads(archive.read("manifest.json"))["count"] == 0
    scan = client.post("/api/workbench/databases/默认数据库/scan", json={}).json()
    assert not scan["imported"]
    assert client.get("/api/workbench/profiles").json()["total"] == 0
    # Explicit save restores a logically deleted sample without changing its hash.
    restored = client.post("/api/workbench/profiles", json=body)
    assert restored.status_code == 201, restored.text
    assert restored.json()["geometry_id"] == identifier


def test_delete_does_not_cross_databases(client):
    assert client.post("/api/workbench/databases", json={"name": "第二数据库"}).status_code == 201
    body = request_body("custom")
    identifier = client.post("/api/workbench/profiles", json=body).json()["geometry_id"]
    assert (
        client.post("/api/workbench/profiles", json={**body, "database": "第二数据库"}).status_code
        == 201
    )
    assert client.delete("/api/workbench/profiles/" + identifier).status_code == 200
    assert (
        client.get("/api/workbench/profiles/" + identifier + "?database=第二数据库").status_code
        == 200
    )
    assert client.delete("/api/workbench/profiles/not-present").status_code == 404


def test_delete_formal_geometry_does_not_damage_dataset(tmp_path):
    geometry = _seed_v2(tmp_path / "dataset")
    with TestClient(
        create_app(tmp_path / "dataset", profile_database_root=tmp_path / "databases")
    ) as client:
        response = client.get("/api/workbench/profiles").json()
        assert response["items"][0]["label"].startswith("自定义")
        assert client.delete("/api/workbench/profiles/" + geometry.geometry_id).status_code == 200
        assert client.get("/api/workbench/profiles").json()["total"] == 0
        assert client.get("/api/v2/geometries/" + geometry.geometry_id).status_code == 200
        assert client.app.state.store_v2.count() == 1


def test_label_is_display_only_and_old_exports_remain_unchanged(tmp_path):
    body = request_body("custom")
    record = generate_record(body["family"], body["spec"])
    identifier = record["geometry_id"]
    record.pop("label")
    library = ProfileLibrary(tmp_path / "library")
    library.save(record)
    snapshot = {p: p.read_bytes() for p in library.root.glob(identifier + ".*")}
    assert library.get(identifier)["label"].startswith("自定义")
    library.save(generate_record(body["family"], body["spec"]))
    for p, data in snapshot.items():
        assert p.read_bytes() == data


def test_deleted_import_does_not_reappear_after_scan(client):
    from cascade_forge.domain.coordinates import coordinate_text

    record = generate_record(**request_body("custom"))
    library = client.app.state.profile_library
    path = library.root / "external.dat"
    path.write_text(coordinate_text(record, "dat"), encoding="utf-8")
    scan = client.post("/api/workbench/databases/默认数据库/scan", json={}).json()
    identifier = scan["imported"][0]
    assert client.delete("/api/workbench/profiles/" + identifier).status_code == 200
    assert path.exists()
    assert client.post("/api/workbench/databases/默认数据库/scan", json={}).json()["imported"] == []
    assert client.get("/api/workbench/profiles").json()["total"] == 0


def test_domain_has_no_storage_or_http_dependencies():
    root = Path(__file__).parents[1] / "src/cascade_forge/domain"
    for path in root.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(
                module.startswith(("cascade_forge.storage", "cascade_forge.api", "fastapi"))
                for module in modules
            ), path.name
