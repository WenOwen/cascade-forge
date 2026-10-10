from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from cascade_forge.database_folders import parse_dat
from cascade_forge.models_naca65 import NACA65Spec
from cascade_forge.profile_library import coordinate_text, generate_record
from cascade_forge.web import create_app


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(tmp_path / "v1", profile_database_root=tmp_path / "databases"))


def native_request(database="默认数据库"):
    return {"database": database, "family": "naca65",
            "spec": NACA65Spec(family="65-compressor").model_dump(mode="json")}


def test_named_databases_are_isolated_and_save_coordinate_files(client):
    response = client.post("/api/workbench/databases", json={"name": "我的叶型"})
    assert response.status_code == 201, response.text
    directory = Path(response.json()["path"])
    saved = client.post("/api/workbench/profiles", json=native_request("我的叶型"))
    assert saved.status_code == 201, saved.text
    identifier = saved.json()["geometry_id"]
    for suffix in ("csv", "dat", "json"):
        assert (directory / f"{identifier}.{suffix}").is_file()
    assert client.get("/api/workbench/profiles").json()["total"] == 0
    assert client.get("/api/workbench/profiles?database=我的叶型").json()["total"] == 1
    assert client.get(f"/api/workbench/profiles/{identifier}").status_code == 404
    assert client.get(f"/api/workbench/profiles/{identifier}?database=我的叶型").status_code == 200
    repeated = client.post("/api/workbench/profiles", json=native_request("我的叶型"))
    assert repeated.status_code == 201
    scan = client.post("/api/workbench/databases/我的叶型/scan").json()
    assert len(scan["skipped"]) == 1 and not scan["imported"] and not scan["errors"]


@pytest.mark.parametrize("name", [
    "", "..", "../outside", "x/y", "x\\y", "CON", "AUX.txt", "bad?", "历史生成数据",
])
def test_invalid_database_names_are_rejected(client, name):
    assert client.post("/api/workbench/databases", json={"name": name}).status_code == 422


def test_dat_drop_scan_preview_and_reimport_preserve_coordinates(client):
    directory = client.app.state.database_folders.library("默认数据库").root
    native = generate_record("naca65", native_request()["spec"])
    path = directory / "用户叶型.dat"
    path.write_text(coordinate_text(native, "dat"), encoding="utf-8")
    original = path.read_bytes()
    result = client.post("/api/workbench/databases/默认数据库/scan").json()
    assert not result["errors"], result
    identifier = result["imported"][0]
    restored = client.get(f"/api/workbench/profiles/{identifier}").json()
    assert restored["family"] == "imported" and restored["name"] == "用户叶型"
    assert restored["spec"] == {}
    exported = client.get(f"/api/workbench/profiles/{identifier}/export/dat")
    np.testing.assert_allclose(np.loadtxt(io.StringIO(exported.text), skiprows=1),
                               np.loadtxt(io.StringIO(original.decode()), skiprows=1), atol=1e-15)
    assert path.read_bytes() == original
    client.post("/api/workbench/databases/默认数据库/scan")
    assert client.get("/api/workbench/profiles?family=imported").json()["total"] == 1
    path.write_text(coordinate_text(native, "dat").replace("\n", "\r\n"), encoding="utf-8")
    client.post("/api/workbench/databases/默认数据库/scan")
    assert client.get("/api/workbench/profiles?family=imported").json()["total"] == 1


def test_invalid_dat_has_per_file_error_and_is_not_modified(client):
    directory = client.app.state.database_folders.library("默认数据库").root
    path = directory / "bad.dat"
    path.write_text("single surface\n0 0\n0.5 0.1\n1 0\n", encoding="utf-8")
    original = path.read_bytes()
    result = client.post("/api/workbench/databases/默认数据库/scan").json()
    assert len(result["errors"]) == 1 and result["errors"][0]["file"] == "bad.dat"
    assert path.read_bytes() == original
    assert client.get("/api/workbench/profiles").json()["total"] == 0


def test_native_save_does_not_overwrite_modified_user_file(client):
    first = client.post("/api/workbench/profiles", json=native_request())
    assert first.status_code == 201
    directory = client.app.state.database_folders.library("默认数据库").root
    path = directory / f"{first.json()['geometry_id']}.dat"
    path.write_text("user data", encoding="utf-8")
    assert client.post("/api/workbench/profiles", json=native_request()).status_code == 422
    assert path.read_text(encoding="utf-8") == "user data"


def test_imported_dat_never_guesses_dimensional_placement(client):
    directory = client.app.state.database_folders.library("默认数据库").root
    record = generate_record("naca65", native_request()["spec"])
    (directory / "sample.dat").write_text(coordinate_text(record, "dat"), encoding="utf-8")
    identifier = client.post("/api/workbench/databases/默认数据库/scan").json()["imported"][0]
    response = client.post("/api/workbench/geomturbo", json={"name": "blade", "blade_count": 12,
        "sections": [{"geometry_id": identifier, "span_fraction": i, "radius_m": 0.2 + 0.1 * i,
                      "chord_m": 0.05, "stagger_deg": 0} for i in range(2)]})
    assert response.status_code == 422


def test_closed_xyz_dat_and_nonplanar_rejection(tmp_path):
    record = generate_record("naca65", native_request()["spec"])
    lines = coordinate_text(record, "dat").splitlines()[1:]
    path = tmp_path / "igg.dat"
    path.write_text("curve\nXYZ\n" + str(len(lines)) + "\n" +
                    "\n".join(line + " 0" for line in lines), encoding="utf-8")
    assert parse_dat(path)["family"] == "imported"
    path.write_text(path.read_text(encoding="utf-8").replace(" 0\n", " 1\n", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="Z 坐标"):
        parse_dat(path)
