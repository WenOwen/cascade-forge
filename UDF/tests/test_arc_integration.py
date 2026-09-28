"""圆弧族叶型的 CLI 与 Web 集成测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cascade_forge.cli import app
from cascade_forge.web import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
V1_DATASET = REPO_ROOT / "datasets" / "geometry-v1.0.0"


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
    return TestClient(create_app(str(tmp_path / "geometry-v1.0.0")))


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


def test_arc_web_page_and_offline_assets(client: TestClient) -> None:
    page = client.get("/arc")
    assert page.status_code == 200
    assert "圆弧族实验室" in page.text
    assert 'data-arc-root' in page.text
    script = client.get("/static/arc.js")
    assert script.status_code == 200
    assert "arc-profile-chart" in script.text


def test_arc_web_generate_returns_the_closed_form_geometry(client: TestClient) -> None:
    response = client.post("/api/v2/arc/generate", json=_base_spec())
    assert response.status_code == 200
    geometry = response.json()["geometry"]
    assert geometry["features"]["arc_count"] == 2.0
    assert geometry["features"]["trailing_edge_half_wedge_deg"] == pytest.approx(6.0, abs=1e-9)
    assert geometry["diagnostics"]["chord_closure_error"] < 1e-12
    assert geometry["diagnostics"]["node_tangency_error"] < 1e-14
    assert len(geometry["suction"]) == len(geometry["pressure"]) == 201
    assert geometry["profile_id"].startswith("arcgeo_")
    assert geometry["mean_line_id"].startswith("arcml_")


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
        "/api/v2/arc/generate",
        json={**spec, "trailing_edge_half_wedge_deg": ceiling - 0.01},
    )
    assert accepted.status_code == 200, accepted.text
    measured = accepted.json()["geometry"]["features"]["trailing_edge_half_wedge_deg"]
    assert measured == pytest.approx(ceiling - 0.01, abs=1e-3)

    # 上界之上必须被拒绝，并给出可达上界
    rejected = client.post(
        "/api/v2/arc/generate",
        json={**spec, "trailing_edge_half_wedge_deg": ceiling + 0.01},
    )
    assert rejected.status_code == 422
    assert "最大半楔角" in rejected.json()["detail"]


def test_arc_web_reports_junction_station_outside_the_reachable_range(
    client: TestClient,
) -> None:
    spec = _base_spec()
    spec["mean_line"] = {
        **spec["mean_line"],
        "closure": "junction_station",
        "junction_station": 0.95,
    }
    response = client.post("/api/v2/arc/generate", json=spec)
    assert response.status_code == 422
    # ArcMeanLineSpec 的合同校验在进入生成器之前就会拒绝，并给出可达区间。
    payload = json.dumps(response.json(), ensure_ascii=False)
    assert "可达区间" in payload


def test_arc_web_supports_the_multiple_arc_family(client: TestClient) -> None:
    spec = _base_spec()
    spec["mean_line"] = {
        "family": "multiple",
        "inlet_metal_angle_deg": 25.0,
        "outlet_metal_angle_deg": 25.0,
        "interior_node_angles_deg": [12.5, 0.0, -12.5],
        "interior_node_stations": [0.25607, 0.5],
    }
    response = client.post("/api/v2/arc/generate", json=spec)
    assert response.status_code == 200, response.text
    geometry = response.json()["geometry"]
    assert geometry["features"]["arc_count"] == 4.0
    assert geometry["diagnostics"]["node_tangency_error"] < 1e-14


def test_arc_page_is_reachable_from_the_sidebar(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/arc"' in home.text


# --------------------------------------------------------------------------------------
# 界面真实路径的回归：旧集成测试恰好绕开了下面三条（2026-09-23）
# --------------------------------------------------------------------------------------


def test_arc_web_supports_multiple_arc_without_explicit_stations(client: TestClient) -> None:
    """页面只发送内节点切线角；站位必须由后端自动求解。

    旧版只有 CLI 做了这层回退，因此网页端的多圆弧 100% 报
    「多圆弧需要 N-2 = 2 个内节点弦向站位，收到 0 个」——这正是 UI 的真实请求体。
    """

    spec = _base_spec()
    spec["mean_line"] = {
        "family": "multiple",
        "inlet_metal_angle_deg": 25.0,
        "outlet_metal_angle_deg": 25.0,
        "interior_node_angles_deg": [12.5, 0.0, -12.5],
    }
    response = client.post("/api/v2/arc/generate", json=spec)
    assert response.status_code == 200, response.text
    geometry = response.json()["geometry"]
    assert geometry["features"]["arc_count"] == 4.0
    stations = geometry["spec"]["mean_line"]["interior_node_stations"]
    assert len(stations) == 2
    assert all(0.0 < value < 1.0 for value in stations)
    # 回显的站位必须与节点表一致
    assert geometry["node_stations"][1:3] == pytest.approx(stations)


def test_arc_web_supports_the_double_arc_node_angle_closure(client: TestClient) -> None:
    """双圆弧的 ``node_angles`` 闭合需要页面把切点角发过来。

    旧版下拉里有这个选项，但输入框只在 ``family === "multiple"`` 时显示、buildSpec 也只在
    multiple 分支发送该字段，于是这条路径在界面上永远拿不到那个角。
    """

    spec = _base_spec()
    spec["mean_line"] = {
        "family": "double",
        "inlet_metal_angle_deg": 30.0,
        "outlet_metal_angle_deg": 20.0,
        "closure": "node_angles",
        "interior_node_angles_deg": [10.0],
    }
    response = client.post("/api/v2/arc/generate", json=spec)
    assert response.status_code == 200, response.text
    geometry = response.json()["geometry"]
    assert geometry["node_angles_deg"] == pytest.approx([30.0, 10.0, -20.0])

    # 不发送切点角时必须是可读的 422，而不是未处理的 500
    spec["mean_line"].pop("interior_node_angles_deg")
    missing = client.post("/api/v2/arc/generate", json=spec)
    assert missing.status_code == 422
    assert "内节点切线角" in json.dumps(missing.json(), ensure_ascii=False)


def test_arc_web_rejects_unequal_single_arc_angles_with_a_list_detail(
    client: TestClient,
) -> None:
    """单圆弧两端角不等必须 422；``detail`` 是**数组**，前端必须逐条展开。

    直接把数组赋给 ``textContent`` 会渲染成 ``[object Object]``——旧前端的真实缺陷。
    这里固化「detail 是数组且含可读中文消息」这一契约，供前端归一化逻辑依赖。
    """

    spec = _base_spec()
    spec["mean_line"] = {
        "family": "single",
        "inlet_metal_angle_deg": 30.0,
        "outlet_metal_angle_deg": 40.0,
        "closure": "max_camber",
    }
    response = client.post("/api/v2/arc/generate", json=spec)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail
    assert "单圆弧的两端切线角必须相等" in detail[0]["msg"]


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
        "/api/v2/arc/generate",
        json={**spec, "trailing_edge_half_wedge_deg": payload["maximum_feasible_half_wedge_deg"]},
    )
    assert at_ceiling.status_code == 200, at_ceiling.text
    above = client.post(
        "/api/v2/arc/generate",
        json={
            **spec,
            "trailing_edge_half_wedge_deg": payload["maximum_feasible_half_wedge_deg"] + 0.01,
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


def test_arc_page_exposes_the_feasibility_and_node_controls(client: TestClient) -> None:
    """页面必须暴露可行域卡片、站位输入、采样点数与节点表；脚本必须查询 limits。"""

    page = client.get("/arc")
    assert page.status_code == 200
    for marker in (
        'data-arc-bound="wedge_ceiling"',
        'name="interior_node_stations"',
        'name="points_per_side"',
        "data-arc-nodes",
        "可行域",
    ):
        assert marker in page.text, f"页面缺少 {marker!r}"
    script = client.get("/static/arc.js")
    assert script.status_code == 200
    assert "/api/v2/arc/limits" in script.text
    # 错误归一化：数组 detail 必须逐条展开，不能直接当字符串
    assert "Array.isArray(detail)" in script.text
    assert "CFCharts" in script.text


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
