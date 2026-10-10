"""圆弧族叶型（CA / DCA / MCA）的解析不变量测试。

测试只使用独立可验证的解析事实：圆弧几何、闭合条件、极限曲率、退化关系，
不使用实现自身的中间结果作为期望值。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import cascade_forge.circular_arc as arc_kernel
from cascade_forge.circular_arc import (
    ThicknessLawInfeasible,
    aft_end_slope_for_wedge,
    build_arc_chain,
    build_thickness_law,
    cosine_stations,
    default_double_arc_spec,
    generate_arc_profile,
    half_wedge_from_slope_deg,
    junction_station_bounds_deg,
    junction_station_range,
    leading_edge_radius_limit,
    maximum_feasible_half_wedge_deg,
    osculating_radius,
    solve_junction_angle_for_station,
    solve_span_distribution,
    surface_curvature,
    trailing_edge_half_wedges_deg,
)
from cascade_forge.models_circular_arc import (
    ArcFamily,
    ArcMeanLineSpec,
    ArcProfileSpec,
    MeanLineClosure,
    build_mca_mean_line,
)


def _dca(inlet=30.0, outlet=20.0, **overrides):
    mean = ArcMeanLineSpec(
        family=ArcFamily.DOUBLE,
        inlet_metal_angle_deg=inlet,
        outlet_metal_angle_deg=outlet,
        **overrides,
    )
    return ArcProfileSpec(mean_line=mean)


def _thin(spec: ArcProfileSpec) -> ArcProfileSpec:
    """高弯度中弧线在小半径处需要更薄的截面；测试用薄型更接近实际压气机叶型。"""

    return spec.model_copy(
        update={
            "max_thickness_ratio": 0.05,
            "leading_edge_radius_ratio": 0.001,
            "trailing_edge_thickness_ratio": 0.001,
            "trailing_edge_half_wedge_deg": 4.0,
        }
    )


def _circumradius(chain, law, station: float) -> float:
    """真实生成点上的三点外接圆半径（对称步长 ``s/10``）。

    曲率只与曲线本身有关，与被积参数无关，因此把真实的点代入外接圆公式就是一条
    **独立于解析曲率公式**的测量路径。
    """

    step = station / 10.0
    parameters = np.array([station - step, station, station + step])
    frame = chain.evaluate(parameters)
    half = law(parameters)
    points = frame["points"] + half[:, None] * frame["normals"]
    sides = [
        float(np.linalg.norm(points[1] - points[0])),
        float(np.linalg.norm(points[2] - points[1])),
        float(np.linalg.norm(points[2] - points[0])),
    ]
    semi = 0.5 * sum(sides)
    area = math.sqrt(
        max(semi * (semi - sides[0]) * (semi - sides[1]) * (semi - sides[2]), 0.0)
    )
    return sides[0] * sides[1] * sides[2] / (4.0 * area)


def _wedge_from_polyline(suction, pressure) -> float:
    """几何半楔角：两侧叶面**最后两段折线**的切线夹角之半（度）。

    这是唯一与解析换算链无关的楔角度量——它只看生成出来的坐标点。两侧折线在
    尾缘都沿 ``-χ₂`` 方向俯冲（默认 DCA30/20 下约 -26° 与 -14°），故半楔角是
    二者夹角之半，即倾角之差的绝对值的一半。对 ``Δx`` 取绝对值使该度量对折线的
    x 方向平移不敏感，只受点间相对位置影响。

    与 :func:`trailing_edge_half_wedges_deg` 等解析量的区别
    ------------------------------------------------------

    ``features["trailing_edge_half_wedge_deg"]`` 与 ``diagnostics["trailing_edge_*"]``
    都来自 ``trailing_edge_surface_angles_deg(law.aft_end_slope, ...)``，而
    ``law.aft_end_slope`` 本身是 ``aft_end_slope_for_wedge`` 对**同一个**
    ``trailing_edge_surface_angles_deg`` 做 brentq 反解出来的。于是

        ``g(f(β)) = β``      （``f`` = 反解，``g`` = 正向换算）

    是**定义上的恒等式**：无论 ``g`` 写成什么（漏掉弧长因子 ``L``、把楔角定义成
    「弦线到叶面夹角之半」、乃至换成任意单调函数），``f`` 都会把它反解回去，
    ``features`` 于是永远等于请求值。生产代码 ``_verify_law`` 用的是同一条等式，
    同样恒真。教科书的 ``h'(1) = -tan β`` 也只是这条恒等式的一个解，
    所以「自洽」根本不能说明约定正确。

    只有从**生成折线**反算才能发现这类约定错误：实测把 ``g`` 换成「角落减半」的
    错定义后，``build_thickness_law`` 成功、``_verify_law`` 通过、
    ``features`` 仍报 6.0°，而本函数报 12.0°（2 倍偏差）。因此文件里任何
    「楔角 = 请求值」的断言都必须至少有一条走本函数，绝不能只靠 features 字段自证。
    """

    def chord_angle(points) -> float:
        delta = np.asarray(points)[-1] - np.asarray(points)[-2]
        return math.degrees(math.atan2(float(delta[1]), abs(float(delta[0]))))

    return 0.5 * abs(chord_angle(suction) - chord_angle(pressure))


def _stub_arc_kernel(monkeypatch, **replacements) -> None:
    """把 ``cascade_forge.circular_arc`` 里的符号换成本地桩（只影响当前用例）。

    用于验证断言**真的有鉴别力**：把被测函数换成明显错误的实现后，用例必须失败。
    桩必须在测试自身的模块命名空间里定义，因此运行时需要
    ``-p no:assertion`` 关闭 pytest 的断言重写（``python -m pytest`` 在本机沙箱下
    会 ``PermissionError``，默认走 ``artifacts/run_pytest.py``，用法相同）。
    """

    for name, value in replacements.items():
        monkeypatch.setattr(arc_kernel, name, value)


# --------------------------------------------------------------------------------------
# mean line: closure and arc geometry
# --------------------------------------------------------------------------------------


def test_double_arc_nodes_are_close_form_of_the_linear_closure():
    """双圆弧的切点由 sum(delta)=1 与 sum(delta tan mu)=0 闭式给出。"""

    inlet, outlet = 30.0, 20.0
    chain = build_arc_chain(
        ArcMeanLineSpec(
            family=ArcFamily.DOUBLE,
            inlet_metal_angle_deg=inlet,
            outlet_metal_angle_deg=outlet,
        )
    )
    phi0, phi2 = math.radians(inlet), math.radians(-outlet)
    tan1, tan2 = math.tan(0.5 * phi0), math.tan(0.5 * phi2)
    expected_x = tan2 / (tan2 - tan1)
    assert chain.node_points[1, 0] == pytest.approx(expected_x, abs=1e-14)
    assert chain.node_points[1, 1] == pytest.approx(
        expected_x * math.tan(0.5 * phi0), abs=1e-14
    )


def test_each_arc_endpoint_tangent_matches_the_node_angle_exactly():
    chain = build_arc_chain(_dca().mean_line)
    for index, arc in enumerate(chain.arcs):
        assert arc.start == pytest.approx(chain.node_points[index], abs=1e-14)
        assert arc.end == pytest.approx(chain.node_points[index + 1], abs=1e-14)
        # 圆心位于切线的右侧法线方向，故 (点 - 圆心) 平行于左法线
        for point, tangent in (
            (arc.start, arc.start_tangent),
            (arc.end, arc.end_tangent),
        ):
            _, left_normal = arc.frame_at_tangent(tangent)
            radial = point - arc.center
            cross = radial[0] * left_normal[1] - radial[1] * left_normal[0]
            assert abs(float(cross)) == pytest.approx(0.0, abs=1e-14)
            assert np.dot(radial, left_normal) == pytest.approx(arc.radius, rel=1e-14)
            assert np.linalg.norm(radial) == pytest.approx(arc.radius, rel=1e-14)
            unit_tangent = np.array([math.cos(tangent), math.sin(tangent)])
            assert np.dot(radial, unit_tangent) == pytest.approx(0.0, abs=1e-14)


def test_internal_tangency_centers_and_junction_are_collinear():
    """内切圆弧的圆心与切点共线，且落在切点法线上。"""

    chain = build_arc_chain(_dca().mean_line)
    first, second = chain.arcs
    junction = chain.node_points[1]
    direction_first = (junction - first.center) / np.linalg.norm(junction - first.center)
    direction_second = (junction - second.center) / np.linalg.norm(junction - second.center)
    assert direction_first == pytest.approx(direction_second, abs=1e-13)
    tangent = np.array([math.cos(0.0), math.sin(0.0)])
    assert np.dot(direction_first, tangent) == pytest.approx(0.0, abs=1e-13)
    distance = np.linalg.norm(second.center - first.center)
    assert abs(distance - (second.radius - first.radius)) < 1e-13


def test_arc_length_parameterization_reproduces_the_chord_endpoints():
    chain = build_arc_chain(_dca().mean_line)
    endpoints = chain.evaluate(np.array([0.0, 1.0]))["points"]
    assert endpoints[0] == pytest.approx([0.0, 0.0], abs=1e-14)
    assert endpoints[1] == pytest.approx([1.0, 0.0], abs=1e-14)


def test_arc_length_evaluation_is_arc_length_parameterized():
    chain = build_arc_chain(_dca().mean_line)
    parameter = np.linspace(0.0, 1.0, 2001)
    points = chain.evaluate(parameter)["points"]
    increments = np.linalg.norm(np.diff(points, axis=0), axis=1)
    assert np.allclose(increments, increments[0], rtol=1e-9)


def test_evaluate_at_x_is_consistent_with_arc_length_evaluation():
    chain = build_arc_chain(_dca().mean_line)
    parameter = cosine_stations(201)
    points = chain.evaluate(parameter)["points"]
    direct = chain.evaluate_at_x(points[:, 0])
    assert np.allclose(direct["y"], points[:, 1], atol=1e-12)
    assert np.allclose(direct["slope"], np.tan(direct["tangent_angle"]), atol=1e-12)


def test_node_tangent_angles_recover_the_metal_angles():
    for inlet, outlet in ((25.0, 25.0), (30.0, 20.0), (45.0, 12.0), (18.0, 38.0)):
        spec = _thin(_dca(inlet, outlet))
        geometry = generate_arc_profile(spec)
        assert geometry.features["inlet_metal_angle_deg"] == pytest.approx(inlet, abs=1e-12)
        assert geometry.features["outlet_metal_angle_deg"] == pytest.approx(outlet, abs=1e-12)
        assert geometry.features["camber_angle_deg"] == pytest.approx(inlet + outlet, abs=1e-12)
        # 弯度前后分配比是 inlet/(inlet+outlet)，不是 inlet/(inlet+outlet) 的别名之外
        # 还能被别的量替代的量：这里独立重算该比值，而不是复读 features 的同一个字段。
        assert geometry.features["camber_fraction_le"] == pytest.approx(
            inlet / (inlet + outlet), abs=1e-12
        )
        # 独立测量：节点切线角本身由**节点坐标**重算（features 只是复述 node_angles）。
        chain = build_arc_chain(spec.mean_line)
        assert float(np.degrees(chain.node_angles[0])) == pytest.approx(inlet, abs=1e-12)
        assert float(-np.degrees(chain.node_angles[-1])) == pytest.approx(outlet, abs=1e-12)
        # 前缘中弧线切线方向（由生成折线的第一段反算）必须落在请求金属角上。
        camber = np.asarray(geometry.camber)
        first_segment = math.degrees(
            math.atan2(*(camber[min(8, len(camber) - 1)] - camber[0])[::-1])
        )
        assert first_segment == pytest.approx(inlet, abs=2.0)


def test_symmetric_double_arc_is_a_single_arc_of_half_the_camber_angle():
    """chi1 = chi2 时双圆弧严格退化为单圆弧（圆心重合、半径相等）。"""

    single = None
    for camber_half in (10.0, 20.0, 35.0):
        double = build_arc_chain(_dca(camber_half, camber_half).mean_line)
        single = build_arc_chain(
            ArcMeanLineSpec(
                family=ArcFamily.SINGLE,
                inlet_metal_angle_deg=camber_half,
                outlet_metal_angle_deg=camber_half,
            )
        )
        assert double.node_points[1] == pytest.approx([0.5, double.node_points[1, 1]], abs=1e-13)
        assert double.arcs[0].center == pytest.approx(double.arcs[1].center, abs=1e-13)
        assert double.arcs[0].radius == pytest.approx(double.arcs[1].radius, rel=1e-13)
        assert double.arcs[0].radius == pytest.approx(single.arcs[0].radius, rel=1e-13)
        assert double.arcs[0].center == pytest.approx(single.arcs[0].center, abs=1e-13)

    # 单圆弧半径的解析值 R = 1 / (2 sin(chi))
    chi = math.radians(35.0)
    assert single is not None
    assert single.arcs[0].radius == pytest.approx(1.0 / (2.0 * math.sin(chi)), rel=1e-13)


def test_max_camber_closure_places_the_junction_at_the_camber_apex():
    chain = build_arc_chain(_dca(38.0, 17.0).mean_line)
    apex_x, apex_y = chain.apex()
    assert apex_x == pytest.approx(float(chain.node_points[1, 0]), abs=1e-12)
    assert apex_y == pytest.approx(float(chain.node_points[1, 1]), abs=1e-12)
    assert chain.arcs[0].end_tangent == pytest.approx(0.0, abs=1e-15)


def test_junction_station_closure_hits_the_requested_station():
    x_min, x_max = junction_station_range(38.0, 17.0)
    for fraction in (0.1, 0.4, 0.5, 0.618, 0.9):
        station = x_min + fraction * (x_max - x_min)
        spec = _dca(38.0, 17.0, closure=MeanLineClosure.JUNCTION_STATION, junction_station=station)
        chain = build_arc_chain(spec.mean_line)
        assert chain.node_points[1, 0] == pytest.approx(station, abs=1e-12)
        # 切点站位与切点切线角互为单调可逆映射
        recovered = solve_junction_angle_for_station(
            38.0, 17.0, float(chain.node_points[1, 0])
        )
        assert recovered == pytest.approx(float(chain.node_angles[1]) * 180 / math.pi, abs=1e-9)

@pytest.mark.parametrize("inlet,outlet", [(30.0, 20.0), (45.0, 12.0), (12.0, 45.0), (25.0, 25.0)])
def test_junction_angle_for_station_spans_the_reachable_range(inlet, outlet):
    chain = build_arc_chain(_dca(inlet, outlet).mean_line)
    midpoint = float(chain.node_points[1, 0])
    # 凸圆弧链要求 phi_0 > phi_1 > phi_2，故 phi_1 落在 (-chi2, chi1)
    assert -outlet < float(chain.node_angles[1]) * 180 / math.pi < inlet
    x_min, x_max = junction_station_range(inlet, outlet)
    assert 0.0 <= x_min < x_max <= 1.0
    assert x_min < midpoint < x_max
    for fraction in (0.05, 0.5, 0.95):
        station = x_min + fraction * (x_max - x_min)
        solved = solve_junction_angle_for_station(inlet, outlet, station)
        assert -outlet < solved < inlet
        spec = ArcMeanLineSpec(
            family=ArcFamily.DOUBLE,
            inlet_metal_angle_deg=inlet,
            outlet_metal_angle_deg=outlet,
            closure=MeanLineClosure.NODE_ANGLES,
            interior_node_angles_deg=(solved,),
        )
        rebuilt = build_arc_chain(spec)
        assert rebuilt.node_points[1, 0] == pytest.approx(station, rel=1e-9)


def test_symmetric_double_arc_reaches_the_whole_chord():
    """chi1 = chi2 时可达站位恰好是 (0, 1)；非对称时是它的真子区间。"""

    assert junction_station_range(25.0, 25.0) == pytest.approx((0.0, 1.0), abs=1e-9)
    for inlet, outlet in ((30.0, 20.0), (45.0, 12.0), (12.0, 45.0)):
        x_min, x_max = junction_station_range(inlet, outlet)
        assert x_min == 0.0 or x_max == 1.0
        assert (x_min, x_max) != (0.0, 1.0)


@pytest.mark.parametrize("inlet,outlet", [(30.0, 20.0), (45.0, 12.0), (12.0, 45.0)])
def test_junction_station_outside_the_reachable_range_is_rejected(inlet, outlet):
    x_min, x_max = junction_station_range(inlet, outlet)
    if x_min > 0.0:
        with pytest.raises(ValueError, match="超出双圆弧可达区间"):
            solve_junction_angle_for_station(inlet, outlet, 0.5 * x_min)
    if x_max < 1.0:
        with pytest.raises(ValueError, match="超出双圆弧可达区间"):
            solve_junction_angle_for_station(inlet, outlet, 0.5 * (1.0 + x_max))


def test_junction_station_lies_strictly_between_the_attainable_bounds():
    """切点站位与切点切线角互为单调可逆映射；退化极限给出区间的端点。

    ``phi_1 -> -chi1`` 时 ``delta_1 -> 1``（前段平均切线角趋于 0）；``phi_1 -> -chi2``
    时 ``delta_1 -> 0``（后段同样退化）。两个极限值落在可行域的端点或之外，
    因此可达站位是一个真子区间。
    """

    inlet, outlet = 30.0, 20.0
    phi0 = math.radians(inlet)
    phi2 = math.radians(-outlet)
    assert _span_limit(phi0, phi2, -phi0) == pytest.approx(1.0, abs=1e-12)
    assert _span_limit(phi0, phi2, -phi2) == pytest.approx(0.0, abs=1e-12)
    centre = _span_limit(phi0, phi2, 0.0)
    assert 0.0 < centre < 1.0

    lower, upper = junction_station_bounds_deg(inlet, outlet)
    assert (lower, upper) == (-outlet, inlet)
    x_min, x_max = junction_station_range(inlet, outlet)
    raw = (_span_limit(phi0, phi2, phi0), _span_limit(phi0, phi2, phi2))
    assert x_min == pytest.approx(max(0.0, min(raw)), abs=1e-12)
    assert x_max == pytest.approx(min(1.0, max(raw)), abs=1e-12)

    for fraction in (0.02, 0.25, 0.5, 0.75, 0.98):
        station = x_min + fraction * (x_max - x_min)
        solved = solve_junction_angle_for_station(inlet, outlet, station)
        assert lower < solved < upper
        rebuilt = build_arc_chain(
            ArcMeanLineSpec(
                family=ArcFamily.DOUBLE,
                inlet_metal_angle_deg=inlet,
                outlet_metal_angle_deg=outlet,
                closure=MeanLineClosure.NODE_ANGLES,
                interior_node_angles_deg=(solved,),
            )
        )
        assert rebuilt.node_points[1, 0] == pytest.approx(station, rel=1e-9)


def _span_limit(phi0: float, phi2: float, middle: float) -> float:
    from cascade_forge.circular_arc import double_arc_first_span

    return double_arc_first_span(phi0, phi2, middle)


def test_single_arc_rejects_unequal_end_angles():
    with pytest.raises(ValueError, match="单圆弧"):
        ArcMeanLineSpec(
            family=ArcFamily.SINGLE,
            inlet_metal_angle_deg=30.0,
            outlet_metal_angle_deg=20.0,
        )


def test_single_arc_rejects_a_span_that_does_not_close():
    with pytest.raises(ValueError, match="单圆弧"):
        solve_span_distribution(np.radians(np.array([30.0, -20.0])))


# --------------------------------------------------------------------------------------
# multiple arc generalization
# --------------------------------------------------------------------------------------


def _mca(count: int, camber: float = 50.0):
    """``count`` 段等转角圆弧链；内节点站位由 ``build_mca_mean_line`` 自动给出。"""

    step = camber / count
    angles = tuple(0.5 * camber - index * step for index in range(count + 1))
    mean = build_mca_mean_line(angles[0], -angles[-1], angles)
    return ArcProfileSpec(mean_line=mean)


@pytest.mark.parametrize("count", [3, 4, 5, 7])
def test_multiple_arc_chain_closes_and_is_tangent_at_every_node(count):
    spec = _mca(count)
    chain = build_arc_chain(spec.mean_line)
    assert chain.arc_count == count
    assert chain.node_points[0] == pytest.approx([0.0, 0.0], abs=1e-14)
    assert chain.node_points[-1] == pytest.approx([1.0, 0.0], abs=1e-13)
    for previous, current in zip(chain.arcs, chain.arcs[1:], strict=False):
        assert current.start_tangent == pytest.approx(previous.end_tangent, abs=1e-15)
        assert current.start == pytest.approx(previous.end, abs=1e-13)
        direction_previous = (previous.end - previous.center) / previous.radius
        direction_current = (current.start - current.center) / current.radius
        # 内切：圆心连线过切点。两段圆弧同心时为退化情形。
        if np.linalg.norm(current.center - previous.center) > 1e-12:
            assert direction_previous == pytest.approx(direction_current, abs=1e-12)
        else:
            assert previous.radius == pytest.approx(current.radius, rel=1e-12)
    interior = spec.mean_line.interior_node_stations
    assert chain.node_points[1 : len(interior) + 1, 0] == pytest.approx(
        np.array(interior), abs=1e-12
    )
    assert np.all(chain.spans > 0.0)
    assert float(chain.spans.sum()) == pytest.approx(1.0, abs=1e-13)
    assert float(chain.node_points[-1, 1]) == pytest.approx(0.0, abs=1e-13)


@pytest.mark.parametrize("count", [3, 4, 5])
def test_multiple_arc_mca_closes_for_the_full_profile(count):
    geometry = generate_arc_profile(_thin(_mca(count)))
    assert geometry.features["arc_count"] == float(count)
    assert geometry.features["chord_closure_error_ratio"] < 1e-12
    assert geometry.diagnostics["node_tangency_error"] < 1e-14
    # 独立测量：把生成折线按相邻段的夹角重新做一次自交检测。
    # （``diagnostics["self_intersection"]`` 在生产代码里被硬编码成 0.0，断言它
    # 等于 0.0 永远是 True——它记录的是「装配没抛错」这个事实，不是测量结果。）
    from cascade_forge.circular_arc import has_self_intersection

    suction = np.asarray(geometry.suction)
    pressure = np.asarray(geometry.pressure)
    assert not has_self_intersection(np.vstack([suction, pressure[::-1]]))


def test_multiple_arc_curvature_is_piecewise_constant_and_jumps_at_nodes():
    """MCA 中弧线的曲率是分片常值，在节点处跳变。

    等转角对称链的半径关于弦线中点镜像对称（``r_i = r_{N-1-i}``），因此半径不是
    单调序列；要验证的是「分片常值 + 节点跳变 + 对称」这三条。
    """

    chain = build_arc_chain(_mca(4).mean_line)
    curvatures = [arc.curvature for arc in chain.arcs]
    radii = [arc.radius for arc in chain.arcs]
    # 等转角链的半径关于弦线中点镜像对称；内节点处仍然是真实的曲率跳变。
    assert radii == pytest.approx(radii[::-1], rel=1e-12)
    assert 1 < len(set(np.round(curvatures, 12))) < len(curvatures)
    for index in range(1, chain.arc_count):
        position = float(chain.cumulative_length[index]) / chain.total_length
        offset = 1e-6
        left = float(chain.evaluate(np.array([position - offset]))["curvature"][0])
        right = float(chain.evaluate(np.array([position + offset]))["curvature"][0])
        # 等半径的相邻圆弧（对称链的中部节点）不产生跳变，这是正确几何。
        if abs(chain.arcs[index - 1].radius - chain.arcs[index].radius) < 1e-12:
            assert left == pytest.approx(right, rel=1e-12)
        else:
            assert abs(left - right) > 1e-6


def test_multiple_arc_rejects_incomplete_node_data():
    """节点角不足必须报错；但**站位**是派生量，缺省时自动求解而不是报错。

    站位由两条线性闭合条件在仿射可行域内确定，不是自由参数，因此省略它属于合法输入。
    这里同时固化两件事：节点角数量不足仍然拒绝，以及节点角序列不合法时错误信息指向
    真正的原因（递减性），而不是一句笼统的「缺少站位」。
    """

    with pytest.raises(ValueError, match="多圆弧"):
        ArcMeanLineSpec(
            family=ArcFamily.MULTIPLE,
            interior_node_angles_deg=(30.0,),
            interior_node_stations=(),
        )
    # 两个内节点角 + 默认两端角 30/-20 会给出 (30, 30, 10, -20)：首段转角为零，
    # 不是严格递减，必须被拒绝——错误信息应当说明这一点。
    with pytest.raises(ValueError, match="严格递减"):
        ArcMeanLineSpec(
            family=ArcFamily.MULTIPLE,
            interior_node_angles_deg=(30.0, 10.0),
            interior_node_stations=(),
        )
    # 合法序列省略站位时自动解出，且解出的跨度严格为正。
    resolved = ArcMeanLineSpec(
        family=ArcFamily.MULTIPLE,
        inlet_metal_angle_deg=30.0,
        outlet_metal_angle_deg=20.0,
        interior_node_angles_deg=(10.0, -5.0),
        interior_node_stations=(),
    )
    assert len(resolved.interior_node_stations) == resolved.arc_count - 2
    assert all(0.0 < station < 1.0 for station in resolved.interior_node_stations)


def test_non_monotonic_node_angles_are_rejected():
    with pytest.raises(ValueError, match="严格递减"):
        ArcMeanLineSpec(
            family=ArcFamily.MULTIPLE,
            inlet_metal_angle_deg=30.0,
            outlet_metal_angle_deg=20.0,
            interior_node_angles_deg=(-30.0, 10.0),
            interior_node_stations=(0.3,),
        )


def test_infeasible_interior_stations_are_rejected():
    with pytest.raises(ValueError, match="非正"):
        solve_span_distribution(
            np.radians(np.array([40.0, 25.0, 10.0, -20.0])),
            (0.9,),
        )


# --------------------------------------------------------------------------------------
# thickness law
# --------------------------------------------------------------------------------------


def test_thickness_law_hits_its_contract_values():
    spec = _dca()
    law = build_thickness_law(spec)
    split = spec.max_thickness_station
    assert float(law(np.array([0.0]))[0]) == 0.0
    assert float(law(np.array([split]))[0]) == pytest.approx(
        0.5 * spec.max_thickness_ratio, rel=1e-13
    )
    assert abs(float(law.derivative(np.array([split]))[0])) < 1e-3
    assert float(law(np.array([1.0]))[0]) == pytest.approx(
        0.5 * spec.trailing_edge_thickness_ratio, rel=1e-13
    )
    # 末端斜率必须由五次 Hermite 真正实现（比较多项式求值与构造时钉入的端点条件，
    # 而不是再调用一次反解函数——那才是同义反复）。楔角与斜率的换算含 L 与 hκ，
    # 独立的几何验证见 test_trailing_edge_wedge_is_realized_exactly。
    assert float(law.derivative(np.array([1.0]))[0]) == pytest.approx(
        law.aft_end_slope, rel=1e-12
    )
    assert law.aft_end_slope < 0.0


def test_thickness_law_is_second_order_continuous_at_the_split():
    spec = _dca()
    law = build_thickness_law(spec)
    split = spec.max_thickness_station
    epsilon = 1e-7
    for function in (law, law.derivative, law.second_derivative):
        left = float(function(np.array([split - epsilon]))[0])
        right = float(function(np.array([split + epsilon]))[0])
        scale = max(abs(left), abs(right), 1.0)
        assert abs(left - right) <= 1e-5 * scale


def test_thickness_law_leading_edge_has_finite_curvature_radius():
    """``h ~ a1 sqrt(s)`` 给出极限曲率半径 ``R_LE = a1^2 / (2 L)``，与中弧线曲率无关。

    这里 ``build_thickness_law`` 走缺省参数，即 ``L = 1``（直线中弧线的退化条件），
    故 ``R_LE = a1^2 / 2 = r_LE``。``L > 1`` 的装配路径见
    :func:`test_leading_edge_radius_shrinks_by_the_arc_length_factor`。
    """

    for radius in (0.003, 0.006, 0.012):
        spec = _dca().model_copy(update={"leading_edge_radius_ratio": radius})
        law = build_thickness_law(spec)
        assert law.mean_line_length == 1.0
        for mean_curvature in (-2.0, -0.5, 0.0, 1.3):
            limit = leading_edge_radius_limit(law, mean_curvature, 1)
            assert limit == pytest.approx(radius, rel=2e-5)
        assert 0.5 * law.fore_coefficients[0] ** 2 == pytest.approx(radius, rel=1e-14)


def test_leading_edge_radius_shrinks_by_the_arc_length_factor():
    """真实前缘曲率半径是 ``a1^2 / (2 L)``，不是 ``a1^2 / 2``。

    ``s`` 是**归一化**弧长，真实弧长 ``t = L s``，故 ``h = (a1/sqrt(L)) sqrt(t)``，
    鼻部抛物线 ``y^2 = 2 R x`` 的 ``R`` 随之缩短 ``L`` 倍。这里用两种互相独立的
    测量固化该结论：解析曲率公式的 Richardson 极限，以及在真实生成点上的三点外接圆。
    """

    for inlet, outlet in ((30.0, 20.0), (45.0, 45.0)):
        family = ArcFamily.SINGLE if inlet == outlet else ArcFamily.DOUBLE
        spec = ArcProfileSpec(
            mean_line=ArcMeanLineSpec(
                family=family,
                inlet_metal_angle_deg=inlet,
                outlet_metal_angle_deg=outlet,
            ),
            max_thickness_ratio=0.10,
            leading_edge_radius_ratio=0.006,
            trailing_edge_thickness_ratio=0.006,
            trailing_edge_half_wedge_deg=6.0,
            points_per_side=1001,
        )
        chain = build_arc_chain(spec.mean_line)
        length = chain.total_length
        law = build_thickness_law(
            spec,
            arc_length=length,
            outlet_metal_angle_deg=outlet,
            terminal_curvature=chain.arcs[-1].curvature,
        )
        a1 = float(law.fore_coefficients[0])
        true_radius = 0.5 * a1**2 / length
        nominal_radius = 0.5 * a1**2
        assert length > 1.03  # DCA30/20 与 CA45 都满足；两种标称值相差 > 3%
        assert abs(nominal_radius - true_radius) > 0.03 * true_radius

        # 1) 解析：Richardson 外推出的极限值就是 a1^2/(2L)
        limit = leading_edge_radius_limit(law, float(chain.arcs[0].curvature), 1)
        assert limit == pytest.approx(true_radius, rel=1e-5)
        assert abs(limit - nominal_radius) > 1e-3 * nominal_radius

        # 2) 独立测量：真实生成点上的三点外接圆，并用 sqrt(s) 外推到 s -> 0
        far = _circumradius(chain, law, 1e-6)
        near = _circumradius(chain, law, 1e-8)
        extrapolated = near - (far - near) / 9.0  # R(s) = R_0 (1 + O(sqrt s))
        assert extrapolated == pytest.approx(true_radius, rel=1e-3)
        assert abs(extrapolated - nominal_radius) > 0.02 * nominal_radius

        # 3) 装配路径：diagnostics 与密切圆都用真实半径，与请求值分开报告
        geometry = generate_arc_profile(spec)
        assert geometry.diagnostics["leading_edge_radius_limit_ratio"] == pytest.approx(
            true_radius, rel=1e-5
        )
        assert geometry.diagnostics["leading_edge_radius_analytic_ratio"] == pytest.approx(
            true_radius, rel=1e-14
        )
        assert geometry.diagnostics["leading_edge_radius_requested_ratio"] == pytest.approx(
            0.006, rel=1e-14
        )
        assert geometry.features["leading_edge_radius_ratio"] == pytest.approx(0.006, rel=1e-14)
        circle = np.asarray(geometry.leading_edge_circle)
        nose = np.asarray(geometry.camber)[0]
        tangent = np.array([math.cos(math.radians(inlet)), math.sin(math.radians(inlet))])
        center = nose + true_radius * tangent
        assert np.linalg.norm(circle - center, axis=1) == pytest.approx(
            true_radius, rel=1e-12
        )
        assert np.linalg.norm(center - nose) == pytest.approx(true_radius, rel=1e-14)
        assert osculating_radius(law, float(chain.arcs[0].curvature), 1e-6, 1) == pytest.approx(
            _circumradius(chain, law, 1e-6), rel=1e-4
        )


def test_thickness_law_is_positive_and_unimodal():
    spec = _dca()
    law = build_thickness_law(spec)
    stations = np.linspace(1e-9, 1.0, 40001)
    half = law(stations)
    assert np.all(half > 0.0)
    peak = int(np.argmax(half))
    assert stations[peak] == pytest.approx(spec.max_thickness_station, abs=1e-4)
    assert np.all(np.diff(half[:peak]) >= 0.0)
    assert np.all(np.diff(half[peak + 1 :]) <= 0.0)


def test_trailing_edge_wedge_convention_matches_geometry_v1():
    """两个内核共享「半楔角 = 两侧叶面切线夹角之半」的语义，但只在直线中弧线上
    ``h'(1) = -tan(beta)`` 才等于几何楔角。

    ``geometry-v1`` 把厚度参数化成 ``dh/dx|_1 = -tan(beta)``；本内核在同样的直线
    中弧线条件（``L = 1``、``chi_2 = 0``、``kappa = 0``）下必须逐位回到该式子。
    真实的弯中弧线上换算含弧长因子 ``L`` 与曲率项 ``h kappa``，因此要比的是**几何
    实现楔角**：本内核让实现值等于请求值，而 v1 的 ``dh/dx`` 参数并不等于几何楔角
    （同样请求 6°，v1 的实际几何半楔角只有 5.8224°）。旧的同义反复断言
    ``v1_slope == aft_end_slope_for_wedge(6.0)`` 正是在这一点上错的。
    """

    from cascade_forge.geometry2d import _generate_profile_v1, _half_thickness
    from cascade_forge.models import GeometrySpec

    beta = 6.0
    v1 = GeometrySpec(
        inlet_metal_angle_deg=40.0,
        turning_angle_deg=20.0,
        quarter_angle_bias_deg=0.0,
        three_quarter_angle_bias_deg=0.0,
        max_thickness_ratio=0.10,
        max_thickness_x_ratio=0.40,
        leading_edge_radius_ratio=0.006,
        trailing_edge_thickness_ratio=0.006,
        trailing_edge_half_wedge_deg=beta,
        fore_shape_factor=1.0,
        aft_shape_factor=1.0,
    )
    v1_stations = np.linspace(0.0, 1.0, 200001)
    v1_slope = float(np.gradient(_half_thickness(v1, v1_stations), v1_stations)[-1])
    # v1 自己的约定：末端弦向斜率就是 -tan(beta)（独立数值测量，不经本内核；
    # 容差来自差分格式的离散误差，不是约定差异）
    assert v1_slope == pytest.approx(-math.tan(math.radians(beta)), rel=1e-5)

    # 直线中弧线（L = 1、chi_2 = 0、kappa = 0）：本内核与 v1 在同一约定下一致
    # （容差来自 v1_slope 的数值差分误差 ~3e-6）
    assert aft_end_slope_for_wedge(beta, 1.0, 0.0, 0.5 * 0.006, 0.0) == pytest.approx(
        v1_slope, rel=1e-5
    )
    assert half_wedge_from_slope_deg(v1_slope, 1.0, 0.0, 0.5 * 0.006, 0.0) == pytest.approx(
        beta, abs=1e-4
    )

    # 弯中弧线的几何装配：实现楔角等于请求值（折线独立反算，不用 features 自证）
    spec = _dca(30.0, 20.0).model_copy(update={"trailing_edge_half_wedge_deg": beta})
    geometry = generate_arc_profile(spec)
    assert _wedge_from_polyline(geometry.suction, geometry.pressure) == pytest.approx(
        beta, abs=1e-2
    )

    # 反例固化：v1 的 dh/dx 参数**不是**弯中弧线上的几何半楔角
    v1_profile = _generate_profile_v1(v1)
    v1_suction = np.asarray(v1_profile.suction)
    v1_pressure = np.asarray(v1_profile.pressure)
    v1_measured = _wedge_from_polyline(v1_suction, v1_pressure)
    assert v1_measured == pytest.approx(5.8224, abs=1e-3)
    assert v1_measured < beta - 0.1

    # 忽略 L 与曲率项会让末端斜率偏平，进而把实现楔角低估（见下一个测试）
    chain = build_arc_chain(spec.mean_line)
    law = build_thickness_law(
        spec,
        arc_length=chain.total_length,
        outlet_metal_angle_deg=spec.mean_line.outlet_metal_angle_deg,
        terminal_curvature=chain.arcs[-1].curvature,
    )
    assert abs(law.aft_end_slope) > abs(v1_slope)


def test_trailing_edge_wedge_is_realized_exactly():
    """实现楔角必须等于请求值；且该值是弦向几何量，不是 dh/ds。

    刻意走完整装配路径（含弧长因子 L 与曲率项 hκ），因为纯厚度层面的
    退化调用无法发现单位换算错误。

    这里对 ``(30°, 20°)`` 与 ``(80°, 10°)`` 两组金属角、``β = 6°/10°`` 两个楔角
    同时钉住三种量：解析绝对切线角、独立折线反算（见 :func:`_wedge_from_polyline`）、
    以及「把换算里的 ``L`` 丢掉就会偏小」的反向证据。
    ``(80°, 10°)`` 必须留在这里：它的 ``L`` 与默认合同差别最大，
    是 ``L`` 换算错误最容易暴露的工况。

    厚度合同用默认的 ``t/c = 0.10``：``_thin`` 的薄型在 ``s_t = 0.40`` 上只能撑到
    约 7.6°，``β = 10°`` 会（正确地）被判为不可行，测不到 10° 这条边界。
    """

    hte = 0.5 * 0.006
    for inlet, outlet in ((30.0, 20.0), (80.0, 10.0)):
        for requested in (6.0, 10.0):
            spec = _dca(inlet, outlet).model_copy(
                update={"trailing_edge_half_wedge_deg": requested}
            )
            chain = build_arc_chain(spec.mean_line)
            law = build_thickness_law(
                spec,
                arc_length=chain.total_length,
                outlet_metal_angle_deg=spec.mean_line.outlet_metal_angle_deg,
                terminal_curvature=chain.arcs[-1].curvature,
            )
            # 解析量返回的是两侧叶面的**绝对切线角**（``-χ₂ ∓ β``）；折线反算得到的是
            # **几何半楔角**（二者夹角之半）。两条路径必须互相印证。
            angle_suction, angle_pressure = trailing_edge_half_wedges_deg(
                law, spec.mean_line.outlet_metal_angle_deg, chain.arcs[-1].curvature
            )
            geometry = generate_arc_profile(spec)
            measured = _wedge_from_polyline(geometry.suction, geometry.pressure)
            assert angle_suction == pytest.approx(-outlet - requested, abs=0.05)
            assert angle_pressure == pytest.approx(-outlet + requested, abs=0.05)
            assert measured == pytest.approx(requested, abs=1e-2)
            assert measured == pytest.approx(
                0.5 * abs(angle_pressure - angle_suction), abs=0.05
            )
            assert law.half_thickness_te == pytest.approx(hte, rel=1e-14)

            # features 只是 ``g(f(β)) = β`` 恒等式的复述，单独不构成检查；
            # 它必须与上面那条**独立**的折线断言同时成立才有意义。
            assert geometry.features["trailing_edge_half_wedge_deg"] == pytest.approx(
                requested, abs=1e-6
            )
            # 两侧切线角关于中弧线尾缘角对称；残余偏差是曲率项 hκ 的一阶效应
            # （|hκ| ≈ 0.0017，故偏差 ≈ β·|hκ| ≈ 0.01°，而 v1 的垂直偏置会差 0.18°）
            camber = np.asarray(geometry.camber)
            camber_te = math.degrees(math.atan2(*(camber[-1] - camber[-2])[::-1]))
            assert abs(0.5 * (angle_suction + angle_pressure) - camber_te) < 0.05
            # 反解出的 dh/ds 必须比 -tan(beta) 更陡：换算含 L 因子
            assert abs(geometry.diagnostics["aft_end_slope"]) > math.tan(
                math.radians(requested)
            )
            # 两个叶面切线角是绝对角，围绕 -χ₂ 张开（不是半楔角本身，也不是 0）
            assert geometry.diagnostics["trailing_edge_surface_angle_suction_deg"] == (
                pytest.approx(-outlet - requested, abs=0.05)
            )
            assert geometry.diagnostics["trailing_edge_surface_angle_pressure_deg"] == (
                pytest.approx(-outlet + requested, abs=0.05)
            )


def test_wedge_definition_is_not_the_self_consistent_corner_halving_one(monkeypatch):
    """反例固化：楔角不能用「角落/弦线减半」的错定义自证。

    把解析换算 ``trailing_edge_surface_angles_deg`` 换成对 ``-χ₂`` 的**半角**版本
    （一个几何上讲得通、但与合同不符的定义）后：

    * ``build_thickness_law`` 仍然成功——反解 ``aft_end_slope_for_wedge`` 会忠实地
      把这个错定义反解回去；
    * ``features["trailing_edge_half_wedge_deg"]`` 仍报请求值——见
      :func:`_wedge_from_polyline` 的说明，这是 ``g(f(β)) = β`` 恒等式；
    * 只有**从折线独立反算**的量会变成 2 倍。

    这正是「不能只用 features 字段自证」的可执行证据：任何把上面那条折线断言删掉
    的改动，都会让这个测试与 :func:`test_trailing_edge_wedge_is_realized_exactly`
    同时失去唯一的鉴别力来源。
    """

    hte = 0.5 * 0.006
    requested = 6.0
    spec = _dca(30.0, 20.0).model_copy(
        update={"trailing_edge_half_wedge_deg": requested}
    )
    baseline_geometry = generate_arc_profile(spec)
    baseline = _wedge_from_polyline(baseline_geometry.suction, baseline_geometry.pressure)
    assert baseline == pytest.approx(requested, abs=1e-2)
    assert hte > 0.0

    original = arc_kernel.trailing_edge_surface_angles_deg

    def corner_halved(aft_slope, arc_length, outlet_deg, half_thickness_te, terminal_curvature):
        """审稿人的错定义：共用的楔角函数返回「角落减半」的角度。

        它把两侧叶面相对 ``-χ₂`` 的张开量减半——一个几何上讲得通、但与合同
        （半楔角 = 两侧切线夹角之半）不符的定义。
        """

        suction, pressure = original(
            aft_slope, arc_length, outlet_deg, half_thickness_te, terminal_curvature
        )
        return (
            -outlet_deg + 0.5 * (suction + outlet_deg),
            -outlet_deg + 0.5 * (pressure + outlet_deg),
        )

    _stub_arc_kernel(monkeypatch, trailing_edge_surface_angles_deg=corner_halved)
    geometry = generate_arc_profile(spec)
    # 恒等式掩盖了错定义：解析审计量与 features 都仍然「正确」。
    assert geometry.features["trailing_edge_half_wedge_deg"] == pytest.approx(
        requested, abs=1e-6
    )
    # 独立折线反算则如实报出 2 倍偏差。
    broken = _wedge_from_polyline(geometry.suction, geometry.pressure)
    assert broken == pytest.approx(2.0 * requested, abs=1e-2)


def test_wedge_polyline_measurement_is_discriminating_across_metal_angles(monkeypatch):
    """非对称 / 对称 / 大弯度三类金属角 × 两个楔角：折线反算必须全部命中请求值。

    取值覆盖 ``30/20``（非对称）、``45/45``（对称，单圆弧退化的近邻）、
    ``80/10``（大弯度、强非对称）；楔角取 6°（默认）与 10°——后者需要默认的
    ``t/c = 0.10`` 厚度合同才可行（``_thin`` 的上界只有约 7.6°）。

    ``45/45`` 的 ``t_TE/c`` 取 0.003 而不是默认的 0.006：后者在 ``β = 10°`` 上正好
    落在 ``_verify_law`` 峰值判据的网格边界（``s = 0.4001`` 与 ``s_t = 0.4000`` 相差
    恰好一个网格步长）上，会以「最大厚度不等于 max_thickness_station」抛错。这是
    内核峰值检测的既有网格敏感性，不是楔角合同的问题，故本用例刻意避开该点。

    同时给出**反例**：丢掉弧长因子 ``L``（把 ``arc_length`` 换成 1）会让每个工况
    都偏小，折线反算因此必须报出偏差而不是继续「通过」。
    """

    cases = (
        (30.0, 20.0, 0.006),
        (45.0, 45.0, 0.003),
        (80.0, 10.0, 0.006),
    )
    for inlet, outlet, te in cases:
        for requested in (6.0, 10.0):
            spec = _dca(inlet, outlet).model_copy(
                update={
                    "trailing_edge_half_wedge_deg": requested,
                    "trailing_edge_thickness_ratio": te,
                }
            )
            geometry = generate_arc_profile(spec)
            measured = _wedge_from_polyline(geometry.suction, geometry.pressure)
            assert measured == pytest.approx(requested, abs=1e-2), (
                f"{inlet}/{outlet} β={requested}°：折线反算 {measured:.6f}°"
            )

    # 反例：换算里丢掉 L 后，折线反算不再等于请求值（任一工况失败即可）。
    original = arc_kernel.trailing_edge_surface_angles_deg

    def without_arc_length(aft_slope, arc_length, outlet_deg, half_thickness_te, curvature):
        return original(aft_slope, 1.0, outlet_deg, half_thickness_te, curvature)

    _stub_arc_kernel(monkeypatch, trailing_edge_surface_angles_deg=without_arc_length)
    deviations = []
    for inlet, outlet, te in cases:
        spec = _dca(inlet, outlet).model_copy(
            update={"trailing_edge_half_wedge_deg": 6.0, "trailing_edge_thickness_ratio": te}
        )
        geometry = generate_arc_profile(spec)
        deviations.append(
            abs(_wedge_from_polyline(geometry.suction, geometry.pressure) - 6.0)
        )
    assert max(deviations) > 0.1


def test_wedge_slope_conversion_includes_arc_length_and_curvature():
    """dh/ds 与楔角之间存在 L 因子；忽略它会让楔角偏小。

    **关于曲率项 hκ 的诚实说明（负结果）**：半楔角

        ``β = ½ [ atan(m / (L(1-hκ))) + atan(m / (L(1+hκ))) ]``

    对 ``hκ`` 是**偶函数**——两个 ``atan`` 的一阶 ``hκ`` 项正好相消，剩下的是
    ``O((hκ)²)``。实测在默认 DCA30/20 上把 ``hκ`` 置零只让 ``aft_end_slope``
    变化 0.0003%（求解器会把斜率重新解到同一个 β），让折线反算的半楔角变化
    ``1.7e-5`` 度。因此在任何有意义的容差下，**``hκ`` 项都是不可观测的**，
    没有断言能鉴别「换算里漏了 hκ」。本用例只对**可观测**的 ``L`` 因子设断言；
    把 ``hκ`` 写进文档是为了纠正「漏掉 hκ 会显著改变楔角」这一说法——
    真正会显著改变的是 ``L``（3%~23%）。
    """

    spec = _dca(30.0, 20.0).model_copy(update={"trailing_edge_half_wedge_deg": 6.0})
    chain = build_arc_chain(spec.mean_line)
    law = build_thickness_law(
        spec,
        arc_length=chain.total_length,
        outlet_metal_angle_deg=spec.mean_line.outlet_metal_angle_deg,
        terminal_curvature=chain.arcs[-1].curvature,
    )
    naive = -math.tan(math.radians(6.0))  # 忽略换算的错误做法
    assert abs(law.aft_end_slope) > abs(naive)
    wrong = half_wedge_from_slope_deg(
        naive,
        chain.total_length,
        spec.mean_line.outlet_metal_angle_deg,
        law.half_thickness_te,
        chain.arcs[-1].curvature,
    )
    assert wrong < 5.9
    # 弧长因子单独就能解释大部分低估（DCA30/20 的 L = 1.031156）
    assert abs(naive) / abs(law.aft_end_slope) == pytest.approx(1.0 / chain.total_length, rel=6e-3)
    # 把 L 从**正向换算**里换成 1（相当于认为 dh/ds 就是弦向斜率），
    # 反算出的半楔角会明显偏大——这是 L 因子可观测的鉴别点。
    without_arc_length = half_wedge_from_slope_deg(
        law.aft_end_slope,
        1.0,
        spec.mean_line.outlet_metal_angle_deg,
        law.half_thickness_te,
        chain.arcs[-1].curvature,
    )
    assert without_arc_length > 6.1
    assert without_arc_length - 6.0 > 0.15


def test_infeasible_wedge_is_rejected_with_the_reachable_ceiling():
    """楔角超出可行域时必须显式失败，并在消息中给出可达上界。"""

    spec = _dca().model_copy(
        update={"max_thickness_ratio": 0.05, "trailing_edge_half_wedge_deg": 30.0}
    )
    with pytest.raises(ThicknessLawInfeasible, match="最大半楔角") as excinfo:
        build_thickness_law(spec)
    ceiling = maximum_feasible_half_wedge_deg(0.05, 0.40, 0.006)
    assert 0.0 < ceiling < 30.0
    # 消息里的上界必须是真实数值，而不是一句空话
    assert f"{ceiling:.4f}" in str(excinfo.value)


# --------------------------------------------------------------------------------------
# regressions for the corrected feasibility search
# --------------------------------------------------------------------------------------


def _default_contract_ceiling() -> float:
    """默认合同（DCA30/20、t/c=0.10、s_t=0.40、t_TE/c=0.006）的可行楔角上界。"""

    spec = default_double_arc_spec()
    chain = build_arc_chain(spec.mean_line)
    return maximum_feasible_half_wedge_deg(
        spec.max_thickness_ratio,
        spec.max_thickness_station,
        spec.trailing_edge_thickness_ratio,
        arc_length=chain.total_length,
        outlet_metal_angle_deg=spec.mean_line.outlet_metal_angle_deg,
        terminal_curvature=chain.arcs[-1].curvature,
    )


def test_maximum_feasible_wedge_is_about_fourteen_degrees():
    """默认合同的上界 ≈ 14.38°，不是旧实现给出的 11.10°。

    旧实现的判据是「先测 ``|Y| = 200``，不可行就直接返回 None」，于是把一整段可行
    楔角误报为不可行（低估 30% 以上）。这里同时钉住绝对量级与两项参数依赖。
    """

    ceiling = _default_contract_ceiling()
    assert ceiling > 14.0
    assert ceiling == pytest.approx(14.378, abs=0.02)
    # 弧长/曲率因子（L = 1.0312、hκ < 0）只把上界从 14.81 改到 14.38，解释不了 11.10
    assert maximum_feasible_half_wedge_deg(0.10, 0.40, 0.006) == pytest.approx(
        14.806, abs=0.02
    )


def test_wedges_near_the_ceiling_are_accepted_and_beyond_are_rejected():
    """略小于上界必须生成成功；远大于上界必须抛错并给出可达上界。"""

    spec = default_double_arc_spec()
    ceiling = _default_contract_ceiling()
    requested = ceiling - 0.05
    geometry = generate_arc_profile(
        spec.model_copy(update={"trailing_edge_half_wedge_deg": requested})
    )
    # features 只反映 ``g(f(β)) = β`` 恒等式，不构成独立检查；这里改用折线反算，
    # 否则「上界附近仍能实现请求楔角」这句话本身就没有被验证。
    assert _wedge_from_polyline(geometry.suction, geometry.pressure) == pytest.approx(
        requested, abs=1e-2
    )
    from cascade_forge.circular_arc import has_self_intersection

    suction = np.asarray(geometry.suction)
    pressure = np.asarray(geometry.pressure)
    assert not has_self_intersection(np.vstack([suction, pressure[::-1]]))

    with pytest.raises(ThicknessLawInfeasible) as excinfo:
        generate_arc_profile(
            spec.model_copy(update={"trailing_edge_half_wedge_deg": ceiling + 2.0})
        )
    message = str(excinfo.value)
    assert "最大半楔角" in message
    assert f"{ceiling:.4f}" in message


def test_curvature_search_finds_a_feasible_entry_second_derivative_at_twelve_degrees():
    """β = 12° 在默认合同下可行，搜索必须返回解而不是 None。

    旧实现在 ``|Y| = 200`` 不可行时立刻判负，12° 就已经返回 None；可行集其实是
    区间 ``[Y_hi(m), Y_lo(m)]``，必须先把区间扫出来。
    """

    from cascade_forge.circular_arc import _normal_thickness_curvature_search

    spec = default_double_arc_spec()
    chain = build_arc_chain(spec.mean_line)
    hmax = 0.5 * spec.max_thickness_ratio
    hte = 0.5 * spec.trailing_edge_thickness_ratio
    tail = 1.0 - spec.max_thickness_station

    def magnitude(wedge: float) -> float:
        return -aft_end_slope_for_wedge(
            wedge,
            chain.total_length,
            spec.mean_line.outlet_metal_angle_deg,
            hte,
            chain.arcs[-1].curvature,
        )

    for wedge in (6.0, 12.0):
        entry = _normal_thickness_curvature_search(hmax, hte, tail, magnitude(wedge))
        assert entry is not None, f"β={wedge}° 被误判为不可行"
        assert math.isfinite(entry) and entry < 0.0
    # 远超上界（14.38°）时可行域确实为空，此时必须返回 None
    assert _normal_thickness_curvature_search(hmax, hte, tail, magnitude(30.0)) is None


def test_mca_builder_repairs_a_feasible_but_non_positive_weight_solution():
    """``(60, 30, 0, -30)`` 的权重初值不可行，但仿射可行域非空。

    旧实现只做权重初值 + 末两段消元，遇到非正跨度就抛 ``ValueError``，把大量可行域
    非空的序列误判为不可行：在 ``[-80°, 80°]`` 步长 8° 的 21 个角度上枚举全部严格
    递减的 4 元序列（5985 组），旧实现对 4712 组抛错，其中 3392 组（72.0%）可行域
    非空。新实现在初值出现非正跨度时退回到「最大化最小跨度」的线性规划；只有可行域
    真的为空才抛错。
    """

    angles = (60.0, 30.0, 0.0, -30.0)
    mean = build_mca_mean_line(60.0, 30.0, angles)
    stations = mean.interior_node_stations
    assert len(stations) == 1
    assert 0.0 < stations[0] < 1.0
    chain = build_arc_chain(mean)
    assert chain.node_points[-1] == pytest.approx([1.0, 0.0], abs=1e-12)
    assert np.all(chain.spans > 0.0)
    assert float(chain.spans.sum()) == pytest.approx(1.0, abs=1e-12)

    # 显式站位同样能构造合法链（旧实现提示的替代路径必须真的可行）
    explicit = ArcMeanLineSpec(
        family=ArcFamily.MULTIPLE,
        inlet_metal_angle_deg=60.0,
        outlet_metal_angle_deg=30.0,
        interior_node_angles_deg=(30.0, 0.0),
        interior_node_stations=(0.211283,),
    )
    explicit_chain = build_arc_chain(explicit)
    assert explicit_chain.node_points[1, 0] == pytest.approx(0.211283, abs=1e-12)
    assert np.all(explicit_chain.spans > 0.0)


def test_surface_curvature_matches_numerical_differentiation():
    """解析曲率公式对数值几何做交叉验证（按装配路径的真实弧长换算）。

    数值参考用窗口二次最小二乘拟合，因此只在解析上"良态"的区域有可比性。
    三类区域被显式排除，每一类都是已知的数学性质而不是放宽容差：

    1. **圆弧节点**：中弧线曲率在该处跳变，窗口跨越节点时不收敛；
    2. **前缘邻域**：``h ~ a1 sqrt(s)`` 使 ``h''`` 发散，二次拟合在端区收敛极慢；
    3. **数组两端**：单边窗口。

    厚度律的导数是**对归一化弧长**的，曲率公式要求对真实弧长的导数，故这里按
    ``h'/L``、``h''/L²`` 传入——这正是 :func:`generate_arc_profile` 的调用方式。
    实测核心区最大偏差从错误换算的 ``2.6×10⁻²`` 降到 ``3×10⁻⁴``，这本身就是
    「归一化弧长必须换算」的独立证据。
    """

    spec = _dca(32.0, 18.0)
    chain = build_arc_chain(spec.mean_line)
    length = chain.total_length
    law = build_thickness_law(
        spec,
        arc_length=length,
        outlet_metal_angle_deg=spec.mean_line.outlet_metal_angle_deg,
        terminal_curvature=chain.arcs[-1].curvature,
    )
    count, half_window = 40001, 12
    stations = np.linspace(0.05, 0.95, count)
    frame = chain.evaluate(stations)
    half = law(stations)
    junction_station = float(chain.cumulative_length[1]) / chain.total_length
    guard = 40.0 * half_window / count

    for side in (1, -1):
        analytic = surface_curvature(
            frame["curvature"],
            half,
            law.derivative(stations) / length,
            law.second_derivative(stations) / length**2,
            side,
        )
        points = frame["points"] + side * half[:, None] * frame["normals"]
        numeric = np.full(count, np.nan)
        for index in range(half_window, count - half_window):
            window = points[index - half_window : index + half_window + 1]
            offset = stations[index - half_window : index + half_window + 1] - stations[index]
            design = np.column_stack([np.ones_like(offset), offset, 0.5 * offset**2])
            x_coefficients = np.linalg.lstsq(design, window[:, 0], rcond=None)[0]
            y_coefficients = np.linalg.lstsq(design, window[:, 1], rcond=None)[0]
            numeric[index] = (
                x_coefficients[1] * y_coefficients[2] - y_coefficients[1] * x_coefficients[2]
            ) / (x_coefficients[1] ** 2 + y_coefficients[1] ** 2) ** 1.5

        core = (
            (stations > 0.25)
            & (stations < 0.95)
            & (np.abs(stations - junction_station) > guard)
            & np.isfinite(numeric)
        )
        assert core.sum() > 0.6 * count
        # 曲率会穿过零点，相对误差在那里无意义，故只按绝对误差比较。
        assert np.allclose(
            numeric[core], analytic[core], rtol=0.0, atol=5e-3
        ), f"side={side} 核心区曲率偏差过大"
        # 前缘邻域必须仍然有限（发散只允许出现在 s -> 0 的极限处）。
        near_le = (stations > 0.05) & (stations < 0.25) & np.isfinite(numeric)
        assert np.all(np.isfinite(analytic[near_le]))
        # 未换算的导数会把核心区曲率放大，超出门槛——固化「换算不可省」。
        wrong = surface_curvature(
            frame["curvature"],
            half,
            law.derivative(stations),
            law.second_derivative(stations),
            side,
        )
        assert np.max(np.abs(numeric[core] - wrong[core])) > 5e-3


# --------------------------------------------------------------------------------------
# full profile
# --------------------------------------------------------------------------------------


def test_default_double_arc_profile_is_well_formed():
    geometry = generate_arc_profile(default_double_arc_spec())
    suction = np.asarray(geometry.suction)
    pressure = np.asarray(geometry.pressure)
    assert suction.shape == pressure.shape == (201, 2)
    assert np.all(np.isfinite(suction)) and np.all(np.isfinite(pressure))
    assert suction[0] == pytest.approx(pressure[0], abs=1e-14)
    assert np.linalg.norm(suction[0]) < 1e-14
    # 中弧线的尾缘落在 (1, 0)；两侧叶面由法向截断给出。
    chain = build_arc_chain(geometry.spec.mean_line)
    assert chain.node_points[-1] == pytest.approx([1.0, 0.0], abs=1e-14)
    normal = np.asarray(geometry.normals)[-1]
    half_te = 0.5 * float(np.asarray(geometry.thickness)[-1])
    # 尾缘法线与弦线不垂直，故两侧 x 位移等量反号：吸力面在 x>1，压力面在 x<1。
    assert suction[-1] - chain.node_points[-1] == pytest.approx(half_te * normal, abs=1e-12)
    assert pressure[-1] - chain.node_points[-1] == pytest.approx(-half_te * normal, abs=1e-12)
    assert normal[0] > 0.0 and suction[-1, 0] > 1.0 > pressure[-1, 0]
    assert geometry.features["chord_closure_error_ratio"] < 1e-12


def test_surface_thickness_is_symmetric_about_the_mean_line():
    geometry = generate_arc_profile(default_double_arc_spec())
    suction, pressure, camber, thickness = geometry.arrays()
    assert np.allclose(0.5 * (suction + pressure), camber, atol=1e-13)
    assert np.allclose(np.linalg.norm(suction - pressure, axis=1), thickness, atol=1e-13)
    # 厚度列必须真的是 2h(s)，而 h(s) 由**请求的厚度合同**独立重建。
    # （旧断言写的是 ``thickness ≈ 2 * (spec and thickness) / 2``：``spec and
    # geometry.thickness`` 在 Python 里就是 ``geometry.thickness``（非空对象逻辑
    # 求值），于是整条退化成 ``X ≈ X``——把 [1e9, -3, 0.5] 代进去也是 True。）
    spec = geometry.spec
    chain = build_arc_chain(spec.mean_line)
    law = build_thickness_law(
        spec,
        arc_length=chain.total_length,
        outlet_metal_angle_deg=spec.mean_line.outlet_metal_angle_deg,
        terminal_curvature=chain.arcs[-1].curvature,
    )
    stations = np.asarray(geometry.arc_parameter)
    assert np.allclose(thickness, 2.0 * law(stations), atol=1e-13)
    # 合同值也必须命中：s_t 处 2h = t/c，尾缘处 2h = t_TE/c。
    assert thickness[int(np.argmax(thickness))] == pytest.approx(
        spec.max_thickness_ratio, rel=1e-6
    )
    assert thickness[-1] == pytest.approx(spec.trailing_edge_thickness_ratio, rel=1e-12)


def test_normal_offset_is_orthogonal_to_the_mean_line_tangent():
    geometry = generate_arc_profile(default_double_arc_spec())
    suction, pressure, camber, _ = geometry.arrays()
    chain = build_arc_chain(geometry.spec.mean_line)
    parameters = np.asarray(geometry.arc_parameter)
    tangents = chain.evaluate(parameters)["tangents"]
    for offset in (suction - camber, pressure - camber):
        projection = np.abs(np.sum(offset * tangents, axis=1))
        assert np.max(projection) < 1e-13


def test_suction_and_pressure_are_free_of_self_intersection_and_ordered():
    geometry = generate_arc_profile(default_double_arc_spec())
    suction, pressure, _, _ = geometry.arrays()
    closed = np.vstack([suction, pressure[::-1]])
    from cascade_forge.circular_arc import has_self_intersection

    assert not has_self_intersection(closed)
    assert np.all(np.abs(suction[:, 1]) > np.abs(pressure[:, 1]) - 1e-12)


def test_surface_endpoints_lie_on_the_edges_of_the_blade():
    """前缘鼻点在 (0,0)；尾缘两侧点位由法向截断给出，且 x 略大于 1。

    中弧线在尾缘以 ``-chi2`` 俯冲，其外法线指向下游，因此法向偏置后的两侧点
    必然位于 ``x = 1`` 之后——这是**正确**的几何结果，不是闭合误差。
    """

    geometry = generate_arc_profile(default_double_arc_spec())
    suction, pressure, camber, thickness = geometry.arrays()
    assert suction[0] == pytest.approx([0.0, 0.0], abs=1e-14)
    assert pressure[0] == pytest.approx([0.0, 0.0], abs=1e-14)
    offset = suction[-1] - camber[-1]
    assert np.linalg.norm(offset) == pytest.approx(0.5 * thickness[-1], rel=1e-12)
    assert camber[-1] == pytest.approx([1.0, 0.0], abs=1e-12)
    normal = np.asarray(geometry.normals)[-1]
    assert offset == pytest.approx(0.5 * thickness[-1] * normal, abs=1e-12)
    assert (pressure[-1] - camber[-1]) == pytest.approx(-0.5 * thickness[-1] * normal, abs=1e-12)
    # 尾缘法线指向下游，因此吸力面在弦线之外、压力面在弦线之内。
    assert suction[-1, 0] > 1.0 > pressure[-1, 0]


def test_profile_id_is_deterministic_and_sensitive_to_every_input():
    base = default_double_arc_spec()
    assert base.profile_id == default_double_arc_spec().profile_id
    variants = [
        base.model_copy(update={"max_thickness_ratio": 0.11}),
        base.model_copy(update={"leading_edge_radius_ratio": 0.007}),
        base.model_copy(update={"trailing_edge_thickness_ratio": 0.007}),
        base.model_copy(update={"trailing_edge_half_wedge_deg": 7.0}),
        base.model_copy(update={"max_thickness_station": 0.41}),
        base.model_copy(update={"points_per_side": 301}),
        _dca(31.0, 20.0),
        _dca(30.0, 21.0),
    ]
    identifiers = {variant.profile_id for variant in variants}
    assert base.profile_id not in identifiers
    assert len(identifiers) == len(variants)


def test_mean_line_id_ignores_thickness_and_display_only_parameters():
    base = _dca()
    variant = base.model_copy(update={"max_thickness_ratio": 0.12, "points_per_side": 301})
    assert base.mean_line.mean_line_id == variant.mean_line.mean_line_id
    other = _dca(31.0, 20.0)
    assert other.mean_line.mean_line_id != base.mean_line.mean_line_id


def test_geometry_rebuilds_coordinates_from_the_specification():
    spec = default_double_arc_spec()
    first = generate_arc_profile(spec)
    second = generate_arc_profile(ArcProfileSpec.model_validate(spec.model_dump(mode="json")))
    assert first.suction == second.suction
    assert first.pressure == second.pressure
    assert first.profile_id == second.profile_id


@pytest.mark.parametrize(
    "inlet,outlet",
    [(12.0, 12.0), (25.0, 25.0), (40.0, 25.0), (25.0, 40.0), (50.0, 20.0)],
)
def test_profile_is_well_formed_across_the_metal_angle_domain(inlet, outlet):
    geometry = generate_arc_profile(_thin(_dca(inlet, outlet)))
    assert geometry.features["chord_closure_error_ratio"] < 1e-12
    assert geometry.diagnostics["pressure_offset_clearance_min"] > 0.0
    assert geometry.diagnostics["node_tangency_error"] < 1e-14
    # 自交必须真的重算一遍：diagnostics 里那个键是常量 0.0。
    from cascade_forge.circular_arc import has_self_intersection

    suction = np.asarray(geometry.suction)
    pressure = np.asarray(geometry.pressure)
    assert not has_self_intersection(np.vstack([suction, pressure[::-1]]))


@pytest.mark.parametrize(
    "inlet,outlet",
    [(12.0, 12.0), (20.0, 45.0), (45.0, 12.0), (60.0, 30.0), (60.0, 60.0)],
)
def test_mean_line_chain_is_exact_across_the_wide_metal_angle_domain(inlet, outlet):
    """中弧线几何本身在整个参数域内必须精确；厚度可另行拒绝。"""

    chain = build_arc_chain(_dca(inlet, outlet).mean_line, check_closure=True)
    invariants = __import__(
        "cascade_forge.circular_arc", fromlist=["check_chain_invariants"]
    ).check_chain_invariants(chain)
    assert invariants["node_rebuild_error"] < 1e-12
    assert invariants["chord_closure_error"] < 1e-12
    assert invariants["chord_length_error"] < 1e-12
    assert all(arc.radius > 0.0 for arc in chain.arcs)


def test_thick_profile_with_high_camber_is_rejected_rather_than_silently_broken():
    """厚度超过中弧线最小曲率半径时必须显式失败，而不是产生自交轮廓。

    强非对称双圆弧（``chi1 = 85°``、``chi2 = 5°``）的前段半径只有 ``0.0457``；
    请求的半厚度 ``0.125`` 远超该半径，法向偏置必然自交。内核必须抛错而不是
    静默产出损坏的轮廓。
    """

    spec = ArcProfileSpec(
        mean_line=ArcMeanLineSpec(
            family=ArcFamily.DOUBLE,
            inlet_metal_angle_deg=85.0,
            outlet_metal_angle_deg=5.0,
        ),
        max_thickness_ratio=0.25,
        max_thickness_station=0.5,
        leading_edge_radius_ratio=0.005,
        trailing_edge_thickness_ratio=0.02,
        trailing_edge_half_wedge_deg=5.0,
    )
    with pytest.raises(ArithmeticError, match="曲率半径"):
        generate_arc_profile(spec)


# --------------------------------------------------------------------------------------
# 可行性判据、上界与站位的回归（2026-09-23）
# --------------------------------------------------------------------------------------


def _ceiling_for(spec: ArcProfileSpec, *, with_leading_edge: bool = True) -> float:
    chain = build_arc_chain(spec.mean_line)
    return maximum_feasible_half_wedge_deg(
        spec.max_thickness_ratio,
        spec.max_thickness_station,
        spec.trailing_edge_thickness_ratio,
        arc_length=chain.total_length,
        outlet_metal_angle_deg=spec.mean_line.outlet_metal_angle_deg,
        terminal_curvature=chain.arcs[-1].curvature,
        leading_edge_radius_ratio=(
            spec.leading_edge_radius_ratio if with_leading_edge else None
        ),
    )


def test_search_never_returns_a_solution_that_fails_its_own_gate():
    """搜索返回的 ``Y`` 必须同时满足尾段与前段单调约束。

    这是本轮修复的核心回归。旧的三分搜索只把**尾段**违反量当作目标函数，因此当**前段**
    约束更严时，它返回的「尾段最优点」本身不可行，``_verify_law`` 随即抛错——结果是
    ``maximum_feasible_half_wedge_deg`` 报出的上界之下就生成不出来。合并违反量
    ``max(尾段违反量, 前段违反量)`` 仍是凸函数，因此三分搜索依然成立。

    最小复现（模糊测试报告给出）：``t/c=0.02``、``s_t=0.20``、``t_TE/c=5e-4``、
    ``r_LE=0.006``、``β=0.6°`` 会被旧实现误拒，实测可行 ``Y = -0.0332646``。
    """

    spec = ArcProfileSpec(
        max_thickness_ratio=0.02,
        max_thickness_station=0.20,
        trailing_edge_thickness_ratio=0.0005,
        leading_edge_radius_ratio=0.006,
        trailing_edge_half_wedge_deg=0.6,
    )
    geometry = generate_arc_profile(spec)
    assert geometry.features["trailing_edge_half_wedge_deg"] == pytest.approx(0.6, abs=1e-9)

    contracts = [
        (0.02, 0.20, 0.0005, 0.006, 0.6),
        (0.10, 0.75, 0.006, 0.006, 6.0),
        (0.05, 0.50, 0.0005, 0.006, 8.0),
        (0.10, 0.40, 0.006, 0.006, 6.0),
        (0.25, 0.60, 0.01, 0.006, 10.0),
    ]
    for ratio, station, te_ratio, radius_ratio, wedge in contracts:
        hmax = 0.5 * ratio
        hte = 0.5 * te_ratio
        length = 1.0 - station
        tolerance = arc_kernel._monotone_slope_tolerance(hmax)
        magnitude = -aft_end_slope_for_wedge(wedge, 1.0, 0.0, hte, 0.0)
        entry = arc_kernel._normal_thickness_curvature_search(
            hmax,
            hte,
            length,
            magnitude,
            split=station,
            leading_edge_radius_ratio=radius_ratio,
        )
        if entry is None:
            continue
        fore = arc_kernel._fore_slope_min_ds(hmax, station, radius_ratio, entry)
        assert fore >= -tolerance, (
            f"t/c={ratio} s_t={station} t_TE={te_ratio} β={wedge}: 搜索返回的 Y={entry} "
            f"使前段最小斜率 {fore} 低于容差 {-tolerance}"
        )
        aft = arc_kernel._aft_slope_max_ds(hmax, hte, length, magnitude, entry)
        assert aft <= tolerance


@pytest.mark.parametrize(
    "ratio,station,te_ratio,radius_ratio",
    [
        (0.10, 0.40, 0.006, 0.006),
        (0.10, 0.75, 0.006, 0.006),
        (0.05, 0.50, 0.0005, 0.006),
        (0.18, 0.35, 0.002, 0.01),
        (0.12, 0.55, 0.003, 0.008),
    ],
)
def test_reported_ceiling_is_the_generator_acceptance_boundary(
    ratio, station, te_ratio, radius_ratio
):
    """上界必须两侧都成立：上界处成功，上界之上失败。

    旧实现报出的上界比生成器真正能实现的高，因此用户照着上界调参会直接撞 422。
    这里把「报出上界 == 真实接受边界」钉死到 ``1e-6°``。

    参数组必须选在数学上界**落在参数域内**的合同上：上界被域上限截断时
    「上界之上必失败」是不成立的（域上限之上是模型校验失败，不是厚度律失败），
    那种情形由 :func:`test_feasibility_report_caps_the_ceiling_into_the_domain` 覆盖。
    """

    base = ArcProfileSpec(
        max_thickness_ratio=ratio,
        max_thickness_station=station,
        trailing_edge_thickness_ratio=te_ratio,
        leading_edge_radius_ratio=radius_ratio,
    )
    law_ceiling = _ceiling_for(base)
    domain_max = arc_kernel.TRAILING_EDGE_HALF_WEDGE_MAX_DEG
    # 断言这组参数确实没有退化：数学上界必须落在参数域内，否则失败侧断言是空转的
    assert law_ceiling < math.nextafter(domain_max, 0.0), (
        f"t/c={ratio} s_t={station} 的数学上界 {law_ceiling} 已顶到域上限，该参数组是退化的"
    )

    effective = law_ceiling
    generate_arc_profile(base.model_copy(update={"trailing_edge_half_wedge_deg": effective}))
    with pytest.raises(ThicknessLawInfeasible):
        generate_arc_profile(
            base.model_copy(update={"trailing_edge_half_wedge_deg": effective + 0.01})
        )


def test_feasibility_report_caps_the_ceiling_into_the_domain():
    """可行域报告的上界必须落在参数域内，且同时给出未截断的数学上界。

    ``t/c = 0.25``、``s_t = 0.75``、``t_TE/c = 5e-4`` 的数学上界约 ``53.9°``，高于参数域
    硬上限 ``45°``。旧实现只报数学上界，界面会把滑动条上限设成一个模型不接受的值。
    """

    spec = ArcProfileSpec(
        max_thickness_ratio=0.25,
        max_thickness_station=0.75,
        trailing_edge_thickness_ratio=0.0005,
        leading_edge_radius_ratio=0.0005,
    )
    report = arc_kernel.arc_feasibility_report(spec)
    assert report["thickness_law_half_wedge_ceiling_deg"] > 50.0
    assert report["maximum_feasible_half_wedge_deg"] < 45.0
    assert report["maximum_feasible_half_wedge_deg"] == pytest.approx(45.0, abs=1e-12)
    assert report["wedge_bound_exists"] is True
    # 报出的上界本身必须是合法输入
    ArcProfileSpec(
        **{
            **spec.model_dump(),
            "trailing_edge_half_wedge_deg": report["maximum_feasible_half_wedge_deg"],
        }
    )
    assert report["half_wedge_domain_deg"] == [0.5, 45.0]
    # 该合同几何上确实能生成
    assert report["geometry_feasible"] is True
    assert report["geometry_error"] is None


def test_feasibility_report_reports_no_bound_below_the_domain_floor():
    """数学上界落在参数域下限之下时，必须报 ``null`` 而不是一个非法的数。

    实测 ``t/c=0.02``、``s_t=0.20``、``t_TE/c=0.018`` 的数学上界恰好是 ``0.0``；
    旧实现把它当成「可行上界」报出去，而 ``0.0`` 连模型校验都过不了
    （``gt=0.5``），用户拿到的是一个必然 422 的数字。
    """

    spec = ArcProfileSpec(
        mean_line=ArcMeanLineSpec(
            family=ArcFamily.DOUBLE,
            inlet_metal_angle_deg=30.0,
            outlet_metal_angle_deg=20.0,
        ),
        max_thickness_ratio=0.02,
        max_thickness_station=0.20,
        trailing_edge_thickness_ratio=0.018,
        leading_edge_radius_ratio=0.006,
    )
    report = arc_kernel.arc_feasibility_report(spec)
    assert report["thickness_law_half_wedge_ceiling_deg"] <= 0.5
    assert report["maximum_feasible_half_wedge_deg"] is None
    assert report["wedge_bound_exists"] is False
    assert report["wedge_feasible"] is False
    # 该合同在任何允许的楔角下都无法实现
    for wedge in (0.51, 1.0, 6.0, 44.9):
        with pytest.raises(ThicknessLawInfeasible):
            generate_arc_profile(
                spec.model_copy(update={"trailing_edge_half_wedge_deg": wedge})
            )


def test_feasibility_report_separates_wedge_bounds_from_geometry_feasibility():
    """「楔角可行」不等于「能生成」：几何门禁与楔角无关，必须单独报出来。

    强非对称高弯度中弧线（``χ₁ = 80°``、``χ₂ = 10°``）配厚截面时，压力面法向偏置自交，
    任何楔角都会失败；此时报一个正的楔角上界会误导用户。
    """

    spec = ArcProfileSpec(
        mean_line=ArcMeanLineSpec(
            family=ArcFamily.MULTIPLE,
            inlet_metal_angle_deg=80.0,
            outlet_metal_angle_deg=10.0,
            interior_node_angles_deg=(40.0, 0.0),
        ),
        max_thickness_ratio=0.25,
        max_thickness_station=0.75,
        trailing_edge_thickness_ratio=0.0005,
        leading_edge_radius_ratio=0.0005,
    )
    report = arc_kernel.arc_feasibility_report(spec)
    assert report["geometry_feasible"] is False
    assert report["geometry_error"]
    # 生成失败与楔角无关：换任何一个允许的楔角都失败
    for wedge in (0.51, 6.0, 44.9):
        with pytest.raises(ArithmeticError):
            generate_arc_profile(
                spec.model_copy(update={"trailing_edge_half_wedge_deg": wedge})
            )


def test_aft_only_ceiling_is_a_loose_upper_bound_not_the_acceptance_boundary():
    """省略 ``leading_edge_radius_ratio`` 时只判尾段，返回值是宽松上界。

    旧 docstring 声称前段约束「从不绑定」；实测在 ``t/c=0.05``、``s_t=0.55``、
    ``t_TE/c=5e-4``、``r_LE=0.00125`` 上前段就是绑定约束。这条测试固化「两种口径不同」
    这一事实，避免以后有人再把省略参数的返回值当成对外承诺。
    """

    ratio, station, te_ratio, radius_ratio = 0.05, 0.55, 0.0005, 0.00125
    base = ArcProfileSpec(
        max_thickness_ratio=ratio,
        max_thickness_station=station,
        trailing_edge_thickness_ratio=te_ratio,
        leading_edge_radius_ratio=radius_ratio,
    )
    aft_only = _ceiling_for(base, with_leading_edge=False)
    full = _ceiling_for(base, with_leading_edge=True)
    assert aft_only > full + 0.1, "本用例要求前段约束确实绑定"
    # 取两者之间的楔角：宽松上界说可行，生成器实际拒绝
    between = 0.5 * (aft_only + full)
    assert full < between < aft_only
    with pytest.raises(ThicknessLawInfeasible):
        generate_arc_profile(
            base.model_copy(update={"trailing_edge_half_wedge_deg": between})
        )
    # 真实边界处必须成功
    generate_arc_profile(base.model_copy(update={"trailing_edge_half_wedge_deg": full}))


def test_interior_station_bounds_match_the_real_feasible_set():
    """闭式站位区间必须与 ``solve_span_distribution`` 的真实可行性完全一致。

    区间由「其余站位固定」时的三条线性不等式给出，因此是逐坐标敏感性。这里在区间内外
    各取探针，逐点核对。
    """

    cases = [
        ((30.0, 20.0), (10.0, -5.0), (0.3,)),
        ((25.0, 25.0), (12.5, 0.0, -12.5), (0.25607, 0.5)),
        ((30.0, 20.0), (10.0, -5.0, -12.0), (0.25, 0.45)),
        ((60.0, 30.0), (30.0, 0.0, -30.0), (0.2, 0.45)),
        ((45.0, 45.0), (20.0, 0.0, -20.0), (0.2, 0.5)),
    ]
    probes = 0
    for (inlet, outlet), interior, stations in cases:
        node_angles = np.radians([inlet, *interior, -outlet])
        bounds = arc_kernel.interior_station_bounds(inlet, outlet, interior, stations)
        assert len(bounds) == len(stations)
        for index, (low, high) in enumerate(bounds):
            assert not math.isnan(low), "当前站位可行，区间却为空"
            assert low < stations[index] < high
            for fraction in (0.01, 0.5, 0.99):
                probe = low + fraction * (high - low)
                candidate = list(stations)
                candidate[index] = probe
                solve_span_distribution(node_angles, tuple(candidate))
                probes += 1
            for offset in (1e-6, 1e-3):
                for probe in (low - offset, high + offset):
                    if not 0.0 < probe < 1.0:
                        continue
                    candidate = list(stations)
                    candidate[index] = probe
                    with pytest.raises(ValueError):
                        solve_span_distribution(node_angles, tuple(candidate))
                    probes += 1
    assert probes > 50


def test_interior_station_bounds_report_empty_intervals_explicitly():
    """其余站位已使链不可行时，区间必须是 ``(nan, nan)`` 而不是一个很窄的区间。"""

    bounds = arc_kernel.interior_station_bounds(
        40.0, 10.0, (25.0, 10.0, -5.0), (0.2, 0.4)
    )
    assert all(math.isnan(low) and math.isnan(high) for low, high in bounds)


def test_multi_arc_resolves_interior_stations_automatically():
    """省略站位是合法输入：模型在验证前自动解出一组严格为正的跨度。

    旧版只有 CLI 有这层回退，因此网页端的多圆弧 100% 报「需要 N-2 个内节点弦向站位」。
    """

    resolved = ArcMeanLineSpec(
        family=ArcFamily.MULTIPLE,
        inlet_metal_angle_deg=25.0,
        outlet_metal_angle_deg=25.0,
        interior_node_angles_deg=(12.5, 0.0, -12.5),
    )
    assert len(resolved.interior_node_stations) == resolved.arc_count - 2
    assert all(0.0 < station < 1.0 for station in resolved.interior_node_stations)
    # 与显式调用 build_mca_mean_line 逐位一致
    explicit = build_mca_mean_line(25.0, 25.0, (25.0, 12.5, 0.0, -12.5, -25.0))
    assert resolved.interior_node_stations == explicit.interior_node_stations
    assert resolved.mean_line_id == explicit.mean_line_id
    # 显式给出站位时不被覆盖
    custom = ArcMeanLineSpec(
        family=ArcFamily.MULTIPLE,
        inlet_metal_angle_deg=25.0,
        outlet_metal_angle_deg=25.0,
        interior_node_angles_deg=(12.5, 0.0, -12.5),
        interior_node_stations=(0.25, 0.5),
    )
    assert custom.interior_node_stations == (0.25, 0.5)
    assert custom.mean_line_id != resolved.mean_line_id


def test_arc_evaluation_stays_accurate_for_near_degenerate_node_angles():
    """相邻节点角间距极小时，求值仍必须达到机器精度。

    旧实现用 ``圆心 + r(−sinθ, cosθ)``：半径按 ``1/Δφ`` 发散到 ``1e12`` 以上时两个
    ``O(1)`` 量相减发生灾难性抵消，坐标误差可达弦长的 2%。弦向量形式把
    ``(φ−θ)/2 = local/(2r)`` 直接由弧长参数取出，避免了这一步相减。
    """

    for gap in (1e-9, 1e-11, 1e-13):
        chain = build_arc_chain(
            build_mca_mean_line(45.0, 45.0, (45.0, 20.0, 20.0 - gap, -45.0))
        )
        assert chain.radius_array.max() > 1e9
        parameter = np.linspace(0.0, 1.0, 41)
        points = chain.evaluate(parameter)["points"]
        # 每个节点必须被精确复现：弧长参数在节点处的值就是累计弧长
        for index, arc in enumerate(chain.arcs):
            start = chain.cumulative_length[index] / chain.total_length
            end = chain.cumulative_length[index + 1] / chain.total_length
            for value, expected in (
                (start, chain.node_points[index]),
                (end, chain.node_points[index + 1]),
            ):
                got = chain.evaluate(np.array([value]))["points"][0]
                assert float(np.max(np.abs(got - expected))) < 1e-12
            # 弧对象自身的端点也必须落在节点上（稳定弦向量形式）
            assert float(np.max(np.abs(arc.start - chain.node_points[index]))) < 1e-12
            assert float(np.max(np.abs(arc.end - chain.node_points[index + 1]))) < 1e-12
        # 起点与终点必须精确落在弦线端点上
        assert float(np.max(np.abs(points[0] - [0.0, 0.0]))) < 1e-12
        assert float(np.max(np.abs(points[-1] - [1.0, 0.0]))) < 1e-12


def test_mca_builder_accepts_a_feasible_region_below_the_lp_tolerance():
    """相邻节点角几乎相等时，所需跨度低于 HiGHS 容差，但可行域非空。

    判据 ``min tanμ < 0 < max tanμ`` 是精确的；线性规划会因原始可行性容差 ``1e-7``
    返回 ``t = 0``。此时必须退回到显式内点构造，而不是误报「仿射可行域为空」。
    """

    for base, gap in (
        (45.0, 1e-5),
        (45.0, 1e-6),
        (45.0, 1e-9),
        (80.0, 1e-5),
        (80.0, 1e-6),
        (80.0, 1e-9),
    ):
        spec = build_mca_mean_line(base, base, (base, base - gap, base - 2 * gap, -base))
        chain = build_arc_chain(spec)
        assert all(span > 0.0 for span in spec.interior_node_stations)
        assert float(np.max(np.abs(chain.node_points[-1] - [1.0, 0.0]))) < 1e-12


def test_single_arc_angle_tolerance_is_shared_by_model_and_generator():
    """模型与生成器对「两端切线角相等」必须用同一个**弧度域**判据。

    旧版模型允许 ``|Δχ| ≤ 1e-9`` 度，而生成器要求 ``|tan(Δχ/2)| ≤ 1e-12``（即
    ``|Δχ| ≤ 1.146e-10`` 度）；中间那条缝里模型说合法、生成器抛错。现在两侧都用
    ``|degrees(radians(χ1) − radians(χ2))| ≤ SINGLE_ARC_ANGLE_TOLERANCE_DEG``。

    必须按弧度判定，不能比较原始度数值：``radians(30.0) == radians(29.999999999999996)``
    为真，那种输入生成器接受（转角为零在弧度域上不存在）而按角度判的模型会拒绝。
    """

    tolerance = arc_kernel.SINGLE_ARC_ANGLE_TOLERANCE_DEG

    def model_accepts(delta: float) -> bool:
        try:
            ArcMeanLineSpec(
                family=ArcFamily.SINGLE,
                inlet_metal_angle_deg=30.0,
                outlet_metal_angle_deg=30.0 - delta,
            )
        except ValueError:
            return False
        return True

    def generator_accepts(delta: float) -> bool:
        try:
            build_arc_chain(
                ArcMeanLineSpec(
                    family=ArcFamily.SINGLE,
                    inlet_metal_angle_deg=30.0,
                    outlet_metal_angle_deg=30.0 - delta,
                )
            )
        except ValueError:
            return False
        return True

    # 含弧度域上会塌缩成同一个数的小差值
    for delta in (0.0, 3.552713678800501e-15, 1e-14, tolerance * 0.9, tolerance * 100.0, 1e-9):
        assert model_accepts(delta) == generator_accepts(delta), (
            f"Δχ={delta} 时模型与生成器判定不一致"
        )

    # 判据本身必须落在弧度域：接受的输入在弧度域内的差不超过容差
    for delta in (0.0, 3.552713678800501e-15, tolerance * 0.9):
        assert model_accepts(delta)
        spec = ArcMeanLineSpec(
            family=ArcFamily.SINGLE,
            inlet_metal_angle_deg=30.0,
            outlet_metal_angle_deg=30.0 - delta,
        )
        radian_gap = abs(
            math.degrees(
                math.radians(spec.inlet_metal_angle_deg)
                - math.radians(spec.outlet_metal_angle_deg)
            )
        )
        assert radian_gap <= tolerance

    # 角度差明显超出容差时必须两侧一起拒绝
    for delta in (1e-9, 1e-6, 0.5):
        assert not model_accepts(delta)
        assert not generator_accepts(delta)


def test_double_arc_rejects_an_infeasible_junction_angle_at_the_model_level():
    """严格递减只是**必要**条件；双圆弧的跨度有闭式，模型应提前给出可读原因。

    旧注释把严格递减写成「充要条件」，实测 535 个通过校验的 spec 里有 249 个在
    ``build_arc_chain`` 才抛错。
    """

    with pytest.raises(ValueError, match="不在 \\(0, 1\\) 内"):
        ArcMeanLineSpec(
            family=ArcFamily.DOUBLE,
            inlet_metal_angle_deg=30.0,
            outlet_metal_angle_deg=1.0,
            closure=MeanLineClosure.NODE_ANGLES,
            interior_node_angles_deg=(20.0,),
        )
    # 最大弯度闭合在双圆弧上恒可行（两段平均切线角必然异号）
    feasible = ArcMeanLineSpec(
        family=ArcFamily.DOUBLE,
        inlet_metal_angle_deg=30.0,
        outlet_metal_angle_deg=1.0,
        closure=MeanLineClosure.MAX_CAMBER,
    )
    assert 0.0 < arc_kernel.double_arc_first_span(
        math.radians(feasible.inlet_metal_angle_deg),
        math.radians(-feasible.outlet_metal_angle_deg),
        0.0,
    ) < 1.0


def test_model_rejects_everything_the_generator_would_reject():
    """「模型说合法」必须蕴含「生成器能构造」——本轮修掉的最后一类缝隙。

    三处曾经不一致：

    * 多圆弧显式给定 ``interior_node_stations`` 时模型只查数量/(0,1)/递增，不查
      ``δ > 0``，于是 ``(0.9, 0.95)`` 通过校验却在 ``build_arc_chain`` 抛错；
    * 双圆弧金属角极小时模型接受、生成器报「末两段平均切线角退化」；
    * 两个不同的度数在弧度域塌缩成同一个数时，按角度判的递减检查会放过一个零转角。
    """

    # 多圆弧：站位把末两段跨度压成负数
    with pytest.raises(ValueError, match="无法闭合"):
        ArcMeanLineSpec(
            family=ArcFamily.MULTIPLE,
            inlet_metal_angle_deg=30.0,
            outlet_metal_angle_deg=20.0,
            interior_node_angles_deg=(10.0, -5.0, -12.0),
            interior_node_stations=(0.9, 0.95),
        )
    # 合法站位仍然通过，且生成器也能构造
    good = ArcMeanLineSpec(
        family=ArcFamily.MULTIPLE,
        inlet_metal_angle_deg=30.0,
        outlet_metal_angle_deg=20.0,
        interior_node_angles_deg=(10.0, -5.0, -12.0),
        interior_node_stations=(0.25, 0.45),
    )
    build_arc_chain(good)
    # 双圆弧：极小金属角使跨度方程退化
    with pytest.raises(ValueError, match="退化|严格递减"):
        ArcMeanLineSpec(
            family=ArcFamily.DOUBLE,
            inlet_metal_angle_deg=1e-14,
            outlet_metal_angle_deg=1e-14,
        )
    # 正常量级仍然通过
    build_arc_chain(
        ArcMeanLineSpec(
            family=ArcFamily.DOUBLE,
            inlet_metal_angle_deg=1e-9,
            outlet_metal_angle_deg=1e-9,
        )
    )


def test_feasibility_report_declares_family_specific_boundaries():
    """不适用的边界字段必须是 ``null``，不能伪造数值。"""

    double_report = arc_kernel.arc_feasibility_report(default_double_arc_spec())
    assert double_report["family"] == "double"
    assert double_report["junction_station_range"] is not None
    assert double_report["interior_node_station_bounds"] is None

    multiple_report = arc_kernel.arc_feasibility_report(
        ArcProfileSpec(
            mean_line=ArcMeanLineSpec(
                family=ArcFamily.MULTIPLE,
                inlet_metal_angle_deg=25.0,
                outlet_metal_angle_deg=25.0,
                interior_node_angles_deg=(12.5, 0.0, -12.5),
            )
        )
    )
    assert multiple_report["family"] == "multiple"
    assert multiple_report["junction_station_range"] is None
    assert len(multiple_report["interior_node_station_bounds"]) == 2
    assert multiple_report["leading_edge_radius_limit_ratio"] == pytest.approx(
        multiple_report["leading_edge_radius_requested_ratio"]
        / multiple_report["mean_line_arc_length"],
        rel=1e-12,
    )
