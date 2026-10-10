"""圆弧族叶型的 CLI 与 Web 集成测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cascade_forge.api.app import create_app
from cascade_forge.cli import app


def _base_spec() -> dict:
    return {
        "mean_line": {
            "family": "double",
            "inlet_metal_angle_deg": 30.0,
            "outlet_metal_angle_deg": 20.0,
            "closure": "max_camber",
        },
        "max_thickness_ratio": 0.10,
        "max_thickness_station": 0.40,
        "leading_edge_radius_ratio": 0.006,
        "trailing_edge_thickness_ratio": 0.006,
        "trailing_edge_half_wedge_deg": 6.0,
        "points_per_side": 201,
    }


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with TestClient(
        create_app(tmp_path / "geometry-v2", profile_database_root=tmp_path / "databases")
    ) as client:
        yield client


def test_arc_cli_generates_all_three_families(tmp_path: Path) -> None:
    runner = CliRunner()
    cases = [
        (
            [
                "arc",
                "generate",
                "--family",
                "single",
                "--inlet-metal-angle-deg",
                "25",
                "--outlet-metal-angle-deg",
                "25",
            ],
            1.0,
        ),
        (["arc", "generate", "--family", "double"], 2.0),
        (
            ["arc", "generate", "--family", "multiple", "--interior-node-angles", "12.5,0,-12.5"],
            4.0,
        ),
    ]
    for arguments, expected_arcs in cases:
        destination = tmp_path / f"arc-{expected_arcs:.0f}.json"
        result = runner.invoke(app, [*arguments, "--destination", str(destination)])
        assert result.exit_code == 0, result.output
        payload = json.loads(destination.read_text(encoding="utf-8"))
        assert payload["features"]["arc_count"] == expected_arcs
        assert payload["diagnostics"]["chord_closure_error"] < 1e-12
        assert payload["diagnostics"]["node_tangency_error"] < 1e-14


def test_arc_cli_limits_reports_feasible_domain(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "arc",
            "limits",
            "--max-thickness-ratio",
            "0.10",
            "--max-thickness-station",
            "0.40",
            "--trailing-edge-thickness-ratio",
            "0.006",
            "--inlet-metal-angle-deg",
            "30",
            "--outlet-metal-angle-deg",
            "20",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert 6.0 < payload["maximum_feasible_half_wedge_deg"] < 45.0
    assert payload["junction_station_range"] == [0.0, pytest.approx(0.8062, abs=1e-3)]
    assert payload["junction_angle_bounds_deg"] == [-20.0, 30.0]


def test_arc_cli_rejects_an_infeasible_wedge(tmp_path: Path) -> None:
    runner = CliRunner()
    destination = tmp_path / "bad.json"
    result = runner.invoke(
        app,
        [
            "arc",
            "generate",
            "--trailing-edge-half-wedge-deg",
            "35",
            "--destination",
            str(destination),
        ],
    )
    assert result.exit_code != 0
    assert not destination.exists()


def test_arc_web_limits_matches_the_generator_ceiling(client: TestClient) -> None:
    """``/arc/limits`` 报的上界必须与**同一 spec** 的生成器真实上界一致。

    早期版本没把中弧线几何（总弧长 L、出口金属角 χ₂、尾缘曲率 κ）传给
    ``maximum_feasible_half_wedge_deg``，报的是 L=1、κ=0 的退化值，比真实上界
    高约 3%。那时用 ``ceiling*0.9`` / ``ceiling*3`` 做断言太宽松，抓不到该偏差。
    这里用 ±0.01° 的窄带把上界钉死。
    """

    spec = _base_spec()
    limits = client.post("/api/v2/arc/limits", json=spec)
    assert limits.status_code == 200
    payload = limits.json()
    ceiling = payload["maximum_feasible_half_wedge_deg"]

    # 端点必须报告它所用的中弧线几何，否则无法审计换算是否正确
    assert payload["mean_line_arc_length"] > 1.0
    assert payload["terminal_curvature"] < 0.0

    # 上界之下必须成功
    accepted = client.post(
        "/api/workbench/preview",
        json={
            "family": "double_arc",
            "spec": {**spec, "trailing_edge_half_wedge_deg": ceiling - 0.01},
        },
    )
    assert accepted.status_code == 200, accepted.text
    measured = accepted.json()["native_geometry"]["features"]["trailing_edge_half_wedge_deg"]
    assert measured == pytest.approx(ceiling - 0.01, abs=1e-3)

    # 上界之上必须被拒绝，并给出可达上界
    rejected = client.post(
        "/api/workbench/preview",
        json={
            "family": "double_arc",
            "spec": {**spec, "trailing_edge_half_wedge_deg": ceiling + 0.01},
        },
    )
    assert rejected.status_code == 422
    assert "最大半楔角" in rejected.json()["detail"]


def test_double_arc_is_reachable_from_the_standard_workbench(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/standard"' in home.text
    standard = client.get("/standard")
    assert standard.status_code == 200
    assert 'data-standard-family="double_arc"' in standard.text


# --------------------------------------------------------------------------------------
# 界面真实路径的回归：旧集成测试恰好绕开了下面三条（2026-09-23）
# --------------------------------------------------------------------------------------


def test_arc_web_limits_declares_family_specific_fields(client: TestClient) -> None:
    """不适用于当前族的边界字段必须是 ``null``；多圆弧要给出站位可达区间。"""

    double = client.post("/api/v2/arc/limits", json=_base_spec())
    assert double.status_code == 200
    double_payload = double.json()
    assert double_payload["family"] == "double"
    assert double_payload["junction_station_range"] is not None
    assert double_payload["interior_node_station_bounds"] is None
    assert double_payload["wedge_feasible"] is True

    multiple_spec = _base_spec()
    multiple_spec["mean_line"] = {
        "family": "multiple",
        "inlet_metal_angle_deg": 25.0,
        "outlet_metal_angle_deg": 25.0,
        "interior_node_angles_deg": [12.5, 0.0, -12.5],
    }
    multiple = client.post("/api/v2/arc/limits", json=multiple_spec)
    assert multiple.status_code == 200, multiple.text
    multiple_payload = multiple.json()
    assert multiple_payload["family"] == "multiple"
    assert multiple_payload["junction_station_range"] is None
    assert len(multiple_payload["interior_node_station_bounds"]) == 2
    for low, high in multiple_payload["interior_node_station_bounds"]:
        assert 0.0 <= low < high <= 1.0


def test_arc_web_limits_ceiling_is_inside_the_model_domain(client: TestClient) -> None:
    """报出的上界必须本身就是合法输入——界面据此设置滑动条上限。"""

    spec = _base_spec()
    spec["max_thickness_ratio"] = 0.25
    spec["max_thickness_station"] = 0.75
    spec["trailing_edge_thickness_ratio"] = 0.0005
    spec["leading_edge_radius_ratio"] = 0.0005
    limits = client.post("/api/v2/arc/limits", json=spec)
    assert limits.status_code == 200, limits.text
    payload = limits.json()
    assert payload["wedge_bound_exists"] is True
    assert payload["maximum_feasible_half_wedge_deg"] < 45.0
    assert payload["thickness_law_half_wedge_ceiling_deg"] > 50.0
    assert payload["geometry_feasible"] is True

    at_ceiling = client.post(
        "/api/workbench/preview",
        json={
            "family": "double_arc",
            "spec": {
                **spec,
                "trailing_edge_half_wedge_deg": payload["maximum_feasible_half_wedge_deg"],
            },
        },
    )
    assert at_ceiling.status_code == 200, at_ceiling.text
    above = client.post(
        "/api/workbench/preview",
        json={
            "family": "double_arc",
            "spec": {
                **spec,
                "trailing_edge_half_wedge_deg": payload["maximum_feasible_half_wedge_deg"] + 0.01,
            },
        },
    )
    assert above.status_code == 422


def test_arc_web_limits_reports_no_bound_when_the_contract_is_hopeless(
    client: TestClient,
) -> None:
    """数学上界跌破参数域下限时必须报 ``null``，而不是一个必然 422 的数字。

    旧端点把恰好 0.0 的数学上界当成「可行上界」返回，界面据此设置滑动条上限会把用户
    引到一个无法提交的值上。
    """

    spec = _base_spec()
    spec["max_thickness_ratio"] = 0.02
    spec["max_thickness_station"] = 0.20
    spec["trailing_edge_thickness_ratio"] = 0.018
    spec["leading_edge_radius_ratio"] = 0.006
    payload = client.post("/api/v2/arc/limits", json=spec).json()
    assert payload["maximum_feasible_half_wedge_deg"] is None
    assert payload["wedge_bound_exists"] is False
    assert payload["wedge_feasible"] is False
    assert payload["half_wedge_domain_deg"] == [0.5, 45.0]


def test_arc_web_limits_reports_geometry_infeasibility_separately(client: TestClient) -> None:
    """楔角可行与几何可生成是两件事，端点必须分开报。"""

    spec = _base_spec()
    spec["mean_line"] = {
        "family": "multiple",
        "inlet_metal_angle_deg": 80.0,
        "outlet_metal_angle_deg": 10.0,
        "interior_node_angles_deg": [40.0, 0.0],
    }
    spec["max_thickness_ratio"] = 0.25
    spec["max_thickness_station"] = 0.75
    spec["trailing_edge_thickness_ratio"] = 0.0005
    spec["leading_edge_radius_ratio"] = 0.0005
    payload = client.post("/api/v2/arc/limits", json=spec).json()
    assert payload["geometry_feasible"] is False
    assert "自交" in payload["geometry_error"]
    # 报出的楔角上界只描述楔角，不代表这条 spec 能生成
    assert payload["wedge_bound_exists"] is True


def test_arc_cli_limits_supports_the_multiple_arc_family(tmp_path: Path) -> None:
    """``arc limits --family multiple`` 必须报告站位可达区间，而不是双圆弧切点区间。"""

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "arc",
            "limits",
            "--family",
            "multiple",
            "--inlet-metal-angle-deg",
            "25",
            "--outlet-metal-angle-deg",
            "25",
            "--interior-node-angles",
            "12.5,0,-12.5",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["family"] == "multiple"
    assert payload["junction_station_range"] is None
    bounds = payload["interior_node_station_bounds"]
    assert len(bounds) == 2
    assert bounds[0][0] == pytest.approx(0.0, abs=1e-9)
    assert 0.0 < bounds[1][0] < bounds[1][1] < 1.0
