from __future__ import annotations

import io
import json
import time
from zipfile import ZipFile

import numpy as np
import pyarrow.parquet as pq
import pytest
from fastapi.testclient import TestClient

from cascade_forge.api.app import create_app
from cascade_forge.domain.circular_arc import default_double_arc_spec, generate_arc_profile
from cascade_forge.domain.geomturbo import parse_geomturbo
from cascade_forge.domain.models_naca65 import NACA65Spec
from cascade_forge.domain.naca65 import generate_naca65
from cascade_forge.pages.context import _default_spec_v2


@pytest.fixture()
def client(tmp_path):
    with TestClient(
        create_app(tmp_path / "geometry-v2", profile_database_root=tmp_path / "databases")
    ) as client:
        yield client


def request_body(family):
    return {
        "family": family,
        "spec": {
            "custom": _default_spec_v2().model_dump(mode="json"),
            "naca65": NACA65Spec(family="65-compressor").model_dump(mode="json"),
            "double_arc": default_double_arc_spec().model_dump(mode="json"),
        }[family],
    }


@pytest.mark.parametrize("family", ["custom", "naca65", "double_arc"])
def test_preview_save_read_and_duplicate(client, family):
    body = request_body(family)
    response = client.post("/api/workbench/preview", json=body)
    assert response.status_code == 200, response.text
    record = response.json()
    assert len(record["suction"]) == 201
    assert client.get("/api/workbench/profiles").json()["total"] == 0
    for _ in range(2):
        saved = client.post("/api/workbench/profiles", json=body)
        assert saved.status_code == 201, saved.text
    identifier = saved.json()["geometry_id"]
    restored = client.get(f"/api/workbench/profiles/{identifier}").json()
    assert restored["spec"] == record["spec"]
    assert restored["suction"] == record["suction"]
    assert restored["native_geometry"] == record["native_geometry"]
    assert client.get("/api/workbench/profiles").json()["total"] == 1


@pytest.mark.parametrize("family", ["naca65", "double_arc"])
def test_native_coordinates_are_not_fitted(client, family):
    record = client.post("/api/workbench/preview", json=request_body(family)).json()
    native = (
        generate_naca65(NACA65Spec(family="65-compressor"))
        if family == "naca65"
        else generate_arc_profile(default_double_arc_spec())
    )
    expected = native.normalized_suction if family == "naca65" else native.suction
    assert record["suction"] == expected


@pytest.mark.parametrize("family", ["custom", "naca65", "double_arc"])
@pytest.mark.parametrize("format", ["csv", "dat"])
def test_saved_export_matches_current(client, family, format):
    body = request_body(family)
    current = client.post(f"/api/workbench/export/{format}", json=body)
    assert current.status_code == 200, current.text
    identifier = client.post("/api/workbench/profiles", json=body).json()["geometry_id"]
    saved = client.get(f"/api/workbench/profiles/{identifier}/export/{format}")
    assert saved.content == current.content
    if format == "csv":
        assert saved.text.startswith("side,point,x_over_c,y_over_c")
    else:
        points = np.loadtxt(io.StringIO(saved.text), skiprows=1)
        np.testing.assert_array_equal(points[0], points[-1])
        assert np.isfinite(points).all()


def test_arc_dat_preserves_both_truncated_tail_endpoints(client):
    body = request_body("double_arc")
    record = client.post("/api/workbench/preview", json=body).json()
    text = client.post("/api/workbench/export/dat", json=body).text
    points = np.loadtxt(io.StringIO(text), skiprows=1)
    np.testing.assert_allclose(points[0], record["suction"][-1], atol=1e-15)
    np.testing.assert_allclose(points[-2], record["pressure"][-1], atol=1e-15)
    assert np.linalg.norm(points[-2] - points[-1]) > 0.005


def test_dataset_archive_contains_native_contracts_and_parquet(client):
    for family in ("custom", "naca65", "double_arc"):
        assert client.post("/api/workbench/profiles", json=request_body(family)).status_code == 201
    response = client.get("/api/workbench/dataset/export")
    with ZipFile(io.BytesIO(response.content)) as archive:
        records = json.loads(archive.read("profiles.json"))
        assert len(records) == 3
        assert json.loads(archive.read("manifest.json"))["count"] == 3
        table = pq.read_table(io.BytesIO(archive.read("profiles.parquet")))
        assert table.num_rows == 3
        assert {r["family"] for r in records} == {"custom", "naca65", "double_arc"}
        for record in records:
            assert f"coordinates/{record['geometry_id']}.csv" in archive.namelist()


def test_browser_filter_search_and_pagination(client):
    for family in ("custom", "naca65", "double_arc"):
        client.post("/api/workbench/profiles", json=request_body(family))
    first = client.get("/api/workbench/profiles?limit=2").json()
    second = client.get("/api/workbench/profiles?limit=2&offset=2").json()
    assert first["total"] == second["total"] == 3
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    filtered = client.get("/api/workbench/profiles?family=naca65").json()
    assert filtered["total"] == 1
    identifier = filtered["items"][0]["geometry_id"]
    assert client.get(f"/api/workbench/profiles?search={identifier}").json()["total"] == 1


@pytest.mark.parametrize("family", ["custom", "naca65", "double_arc"])
def test_sweep_generates_distinct_parameterized_profiles(client, family):
    body = {
        **request_body(family),
        "parameter": ("thickness_ratio" if family == "naca65" else "max_thickness_ratio"),
        "minimum": 0.10,
        "maximum": 0.11,
        "count": 2,
    }
    response = client.post("/api/workbench/batches", json=body)
    assert response.status_code == 202, response.text
    identifier = response.json()["job_id"]
    for _ in range(200):
        job = client.get(f"/api/v2/jobs/{identifier}").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "completed", job
    assert job["result"]["generated"] == job["result"]["unique"] == 2, job
    assert client.get(f"/api/workbench/profiles?family={family}").json()["total"] == 2


@pytest.mark.parametrize("family", ["custom", "naca65", "double_arc"])
def test_geomturbo_export_roundtrips_each_family(client, family, tmp_path):
    identifier = client.post("/api/workbench/profiles", json=request_body(family)).json()[
        "geometry_id"
    ]
    schedule = {
        "name": "blade",
        "blade_count": 12,
        "rotation_direction": "ccw",
        "sections": [
            {
                "span_fraction": i,
                "radius_m": 0.2 + i * 0.1,
                "chord_m": 0.04,
                "stagger_deg": 30,
                "geometry_id": identifier,
            }
            for i in range(2)
        ],
    }
    response = client.post("/api/workbench/geomturbo", json=schedule)
    assert response.status_code == 200, response.text
    path = tmp_path / "blade.geomTurbo"
    path.write_bytes(response.content)
    restored = parse_geomturbo(path)
    assert restored["blade_count"] == 12
    assert len(restored["suction"]) == 2
    for section in restored["suction"]:
        assert section.shape == (201, 3)
        assert np.isfinite(section).all()


def test_invalid_generation_does_not_save(client):
    body = request_body("double_arc")
    body["spec"]["trailing_edge_half_wedge_deg"] = 40
    assert client.post("/api/workbench/profiles", json=body).status_code == 422
    assert client.get("/api/workbench/profiles").json()["total"] == 0
    body = {
        **request_body("naca65"),
        "parameter": "nonsense",
        "minimum": 0.1,
        "maximum": 0.2,
        "count": 2,
    }
    assert client.post("/api/workbench/batches", json=body).status_code == 422


def test_existing_workspace_is_browsable_and_deduplicated(client):
    body = request_body("custom")
    saved = client.post("/api/v2/workspace/designs", json={"name": "旧设计", "spec": body["spec"]})
    assert saved.status_code == 201, saved.text
    identifier = saved.json()["geometry_id"]
    assert client.get("/api/workbench/profiles").json()["total"] == 1
    assert client.get(f"/api/workbench/profiles/{identifier}").status_code == 200
    assert client.get(f"/api/workbench/profiles/{identifier}/export/dat").status_code == 200
    client.post("/api/workbench/profiles", json=body)
    assert client.get("/api/workbench/profiles").json()["total"] == 1


def test_existing_formal_dataset_remains_browsable(client):
    from cascade_forge.domain.features import extract_features
    from cascade_forge.domain.geometry import generate_profile
    from cascade_forge.domain.validation import validate_profile

    geometry = generate_profile(_default_spec_v2())
    client.app.state.store_v2.append_geometries(
        [(geometry, extract_features(geometry), validate_profile(geometry), {"proposal_index": 0})]
    )
    listing = client.get("/api/workbench/profiles?limit=1").json()
    assert listing["total"] == 1
    assert listing["items"][0]["geometry_id"] == geometry.geometry_id
    restored = client.get(f"/api/workbench/profiles/{geometry.geometry_id}").json()
    assert restored["suction"] == geometry.suction
    client.post("/api/workbench/profiles", json=request_body("custom"))
    assert client.get("/api/workbench/profiles").json()["total"] == 1


def test_geomturbo_rejects_mixed_families_and_external_curves(client):
    identifiers = [
        client.post("/api/workbench/profiles", json=request_body(family)).json()["geometry_id"]
        for family in ("naca65", "double_arc")
    ]
    schedule = {
        "name": "blade",
        "blade_count": 12,
        "sections": [
            {
                "geometry_id": identifier,
                "span_fraction": i,
                "radius_m": 0.2 + i * 0.1,
                "chord_m": 0.04,
                "stagger_deg": 0,
            }
            for i, identifier in enumerate(identifiers)
        ],
    }
    assert client.post("/api/workbench/geomturbo", json=schedule).status_code == 422
    schedule["sections"][1]["geometry_id"] = identifiers[0]
    schedule["hub_curve_path"] = "missing.dat"
    assert client.post("/api/workbench/geomturbo", json=schedule).status_code == 422


def test_irregular_input_stations_are_preserved_without_resampling(client, tmp_path):
    identifier = client.post("/api/workbench/profiles", json=request_body("naca65")).json()[
        "geometry_id"
    ]
    radii = [0.2, 0.21, 0.3]
    schedule = {
        "name": "blade",
        "blade_count": 12,
        "sections": [
            {
                "geometry_id": identifier,
                "span_fraction": span,
                "radius_m": radius,
                "chord_m": 0.04,
                "stagger_deg": 0,
            }
            for span, radius in zip([0, 0.1, 1], radii, strict=True)
        ],
    }
    response = client.post("/api/workbench/geomturbo", json=schedule)
    assert response.status_code == 200
    path = tmp_path / "irregular.geomTurbo"
    path.write_bytes(response.content)
    parsed = parse_geomturbo(path)
    for side in ("suction", "pressure"):
        for section, radius in zip(parsed[side], radii, strict=True):
            np.testing.assert_allclose(
                np.linalg.norm(section[:, :2], axis=1), radius, atol=1e-13, rtol=0
            )


def test_three_entry_navigation_and_no_benchmark_controls(client):
    home = client.get("/").text
    assert "自定义叶型" in home
    nav = home.split('<nav aria-label="日常设计">')[1].split("</nav>")[0]
    assert nav.count('class="nav-item') == 3
    assert all(f'href="{path}"' in nav for path in ("/studio", "/standard", "/database"))
    assert "数据库维护" not in home
    standard = client.get("/standard").text
    assert "生成并拟合" not in standard and "拟合矩阵" not in standard
    assert "单圆弧" not in standard and "多圆弧" not in standard
    assert "GeomTurbo" in standard and "批量生成数据集" in standard
    assert client.get("/database").status_code == 200


def test_generation_has_compact_canvas_and_switchable_right_rail(client):
    for path in ("/studio", "/standard"):
        page = client.get(path).text
        assert 'class="generation-layout"' in page
        assert "data-profile-rail" in page
        assert 'data-rail-tab="actions"' in page and 'data-rail-tab="saved"' in page
        assert "data-database-select" in page and "data-database-scan" in page
        assert "Spec JSON" not in page and 'name="explicit_spec"' not in page
    studio = client.get("/studio").text
    assert "data-import-spec" in studio and "data-export-spec" in studio
    assert "data-save-workspace" in studio
    assert "保存设计修订" not in studio
    for markup in (studio, client.get("/standard").text):
        primary = markup.split("data-profile-primary-actions>")[1].split("</div>")[0]
        assert all(label in primary for label in ("保存叶型", "导出 CSV", "导出 DAT"))
        assert "data-profile-3d" not in primary and "data-save-workspace" not in primary
        assert "data-output-more" in markup and "data-database-management" in markup
    assert "data-geometry-issues" in studio and "data-geometry-issue-list" in studio
    assert "data-analysis-interior" in studio and "data-analysis-help" in studio
    assert "data-analysis-summary" in studio


def test_database_has_scrollable_selector_and_four_image_comparison(client):
    page = client.get("/database")
    assert page.status_code == 200
    assert 'class="surface-card library-sidebar"' in page.text
    assert 'aria-label="可滚动样本列表"' in page.text
    assert "data-library-charts" in page.text
    assert "data-library-clear" in page.text
    assert "最多 4 个" in page.text
    assert "<table" not in page.text
    for asset in (
        "/static/profile-database.js?v=3.2-three-pages",
        "/static/profile-database.css?v=3.2-three-pages",
    ):
        assert asset in page.text
        assert client.get(asset).status_code == 200
