from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from cascade_forge.web import create_app

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
        "validate_cascade",
        "fit_anchors",
        "build_blade",
        "export_geomturbo",
        "generate_dataset",
        "audit_dataset",
        "jobs_and_artifacts",
        "generate_naca65",
        "fit_naca65",
        "benchmark_naca65",
        "export_naca65",
    }
    assert set(mapping["capabilities"]) == expected
    with TestClient(
        create_app(
            tmp_path / "v1",
            v2_dataset_root=tmp_path / "v2",
            workspace_root=tmp_path / "workspace",
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
    for label in (
        "总览",
        "叶型工作室",
        "数据库",
        "叶型比较",
        "叶栅检查",
        "NASA 锚点",
        "NACA 65 实验室",
        "三维叶片",
        "数据集与批次",
        "审计",
        "Jobs 与制品",
    ):
        assert label in templates
