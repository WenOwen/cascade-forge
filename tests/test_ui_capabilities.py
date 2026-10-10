from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from cascade_forge.api.app import create_app

ROOT = Path(__file__).parents[1]


def test_every_public_workflow_has_a_ui_api_mapping(tmp_path) -> None:
    mapping = yaml.safe_load((ROOT / "ui-capabilities.yaml").read_text(encoding="utf-8"))
    expected = {
        "generate_profile",
        "resolve_curve_controls",
        "validate_profile",
        "extract_features",
        "workspace_save_import_export",
        "database_query_export",
        "compare_profiles",
        "build_blade",
        "export_geomturbo",
        "jobs_and_artifacts",
        "generate_naca65",
        "delete_database_sample",
        "profile_parameter_sweep",
        "standard_arc_limits",
    }
    assert set(mapping["capabilities"]) == expected
    with TestClient(
        create_app(
            tmp_path / "v2",
            workspace_root=tmp_path / "workspace",
            profile_database_root=tmp_path / "databases",
        )
    ) as client:
        for capability in mapping["capabilities"].values():
            response = client.get(capability["ui"])
            assert response.status_code == 200, capability
            assert "Cascade Forge" in response.text


def test_workbench_has_no_external_cdn_and_exposes_all_pages() -> None:
    templates = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "src" / "cascade_forge" / "templates").rglob("*.html")
    )
    assert "cdn." not in templates.lower()
    for label in ("自定义叶型", "标准叶型", "数据库浏览"):
        assert label in templates
    for route in (
        "/anchors",
        "/datasets",
        "/audit",
        "/jobs",
        "/naca65",
        "/arc",
        "/compare",
        "/cascade",
        "/blade",
    ):
        assert f'href="{route}"' not in templates
