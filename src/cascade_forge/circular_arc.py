"""圆弧族叶型内核：``N`` 段内切圆弧中弧线 + 法向对称厚度。

数学定义（完整推导与依据见 ``docs/circular-arc-profiles.md``）
------------------------------------------------------------

弦线局部坐标：前缘 ``(0, 0)``，尾缘 ``(1, 0)``，``y > 0`` 指向吸力面。

1. **中弧线 = N 段内切圆弧链。** 节点 ``J_0 .. J_N`` 处的切线角
   ``phi_0 > phi_1 > ... > phi_N``（以弦线为基准；``phi_0 = +chi1`` 于前缘，
   ``phi_N = -chi2`` 于尾缘）。第 ``i`` 段圆弧由 ``(J_{i-1}, phi_{i-1}, phi_i, r_i)``
   唯一确定，圆心

   .. math::  C_i = J_{i-1} + r_i (\\sin\\phi_{i-1}, -\\cos\\phi_{i-1}),

   弧上的点由切线角参数化：``P(θ) = C_i + r_i (-\\sin θ, \\cos θ)``。

2. **线性闭合。** 令 ``mu_i = (phi_{i-1}+phi_i)/2``、``Δ_i = phi_{i-1}-phi_i``，第
   ``i`` 段的弦向位移是 ``(2 r_i \\sin(Δ_i/2))(\\cos mu_i, \\sin mu_i)``。定义该段的
   **弦向跨度占比** ``delta_i = 2 r_i \\sin(Δ_i/2) \\cos mu_i``，则闭合等价于

   .. math::  \\sum_i delta_i = 1, \\qquad \\sum_i delta_i \\tan mu_i = 0 .

   两条条件对 ``delta`` 是**线性**的，因此圆弧链闭式可解、无需迭代；给定 ``N-2``
   个内节点弦向站位即唯一确定其余两个跨度。半径由
   ``r_i = delta_i / (2 \\sin(Δ_i/2) \\cos mu_i)`` 反解（弦长归一化为 1）。

3. **单圆弧退化。** ``N = 1`` 时闭合条件给出 ``phi_0 + phi_1 = 0``，即
   ``chi1 = chi2``——与「单圆弧两端切线角必相等」一致。``N = 2`` 且 ``chi1 = chi2``
   时两段圆弧圆心重合、半径相等，严格退化为经典单圆弧：双圆弧把单圆弧包含为特例。

4. **厚度。** 沿中弧线**法向**的对称半厚度 ``h(s)``，``s`` 为中弧线归一化弧长。
   前段在 ``q = sqrt(s)`` 中是四次多项式，首项 ``a1 = sqrt(2 r_LE)`` 精确控制前缘
   曲率半径；在 ``s_t`` 处满足 ``h = t/2``、``dh/ds = 0``。后段是五次 Hermite，与
   前段在 ``s_t`` 处二阶连续，并显式满足尾缘厚度与尾缘半楔角（对**实际几何**求解）。

5. **边缘。** ``h(0) = 0`` 使两侧叶面在前缘共点；法向偏置在 ``h ~ a1 sqrt(s)`` 下的
   极限曲率是 ``∓ 2 L / a1^2``，因此前缘是**自然圆头**，极限曲率半径

   .. math::  R_{LE} = \\frac{a_1^2}{2 L} ,\\qquad L = \\text{中弧线总弧长} \\ge 1 .

   这里的 ``L`` 因子不是笔误：``s`` 是**归一化**弧长，真实弧长是 ``t = L s``，故
   ``h = a_1 sqrt(s) = (a_1 / \\sqrt{L}) sqrt(t)``，抛物线鼻部的曲率半径随之缩短
   ``L`` 倍。``R_LE`` 仍与中弧线曲率无关；密切圆圆心落在前缘切线上、距鼻点 ``R_LE``。
   不需要额外圆角拼接，也不存在尖点。尾缘是垂直于中弧线的截断段，长度 ``2 h(1)``。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import brentq

from .models_circular_arc import (
    ARC_ALGORITHM_VERSION,
    ARC_COORDINATE_CONVENTION,
    SINGLE_ARC_ANGLE_TOLERANCE_DEG,
    TRAILING_EDGE_HALF_WEDGE_MAX_DEG,
    TRAILING_EDGE_HALF_WEDGE_MIN_DEG,
    ArcFamily,
    ArcMeanLineSpec,
    ArcProfileGeometry,
    ArcProfileSpec,
    MeanLineClosure,
    build_designation,
)

SOURCE_PROVENANCE = {
    "definition": "N-arc internally tangent circular-arc mean line (CA / DCA / MCA family)",
    "theory": (
        "NASA SP-36, Aerodynamic Design of Axial-Flow Compressors (1965): camber-line "
        "families parameterized by total camber and the leading-edge camber fraction a; "
        "R. Aungier, Axial-Flow Compressors (ASME Press, 2003), ch. 5: double and "
        "multiple circular arc mean lines"
    ),
    "thickness": (
        "cascade-forge geometry-v1 thickness contract "
        "(src/cascade_forge/geometry2d.py::_half_thickness), re-parameterized by "
        "normalized mean-line arc length and applied normal to the mean line"
    ),
    "kernel": "pure Python / NumPy closed form; no CAD kernel and no iterative curve fitting",
}


# --------------------------------------------------------------------------------------
# mean line
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CircularArc:
    """一段圆弧；点由切线角参数化，``θ ∈ [end_tangent, start_tangent]``。

    ``start_point`` 是构造时已知的精确起点（即链上的节点）。给出它之后 ``start``／``end``
    走**弦向量**形式，避免 ``center + r(−sinθ, cosθ)`` 在大半径下的灾难性抵消；
    :func:`check_chain_invariants` 仍然显式使用 :meth:`point_at_tangent` 做朴素重建，
    因为那正是它要审计的对象。
    """

    center: np.ndarray
    radius: float
    start_tangent: float
    end_tangent: float
    start_point: np.ndarray | None = None

    @property
    def turn(self) -> float:
        return self.start_tangent - self.end_tangent

    @property
    def length(self) -> float:
        return self.radius * self.turn

    def _chord_point(self, tangent: float) -> np.ndarray:
        """由已知起点出发的弦向量求值（无灾难性抵消）。"""

        half_turn = 0.5 * (self.start_tangent - tangent)
        middle = 0.5 * (self.start_tangent + tangent)
        chord = 2.0 * self.radius * math.sin(half_turn)
        return self.start_point + chord * np.array([math.cos(middle), math.sin(middle)])

    @property
    def start(self) -> np.ndarray:
        if self.start_point is not None:
            return self.start_point
        return self.point_at_tangent(self.start_tangent)

    @property
    def end(self) -> np.ndarray:
        if self.start_point is not None:
            return self._chord_point(self.end_tangent)
        return self.point_at_tangent(self.end_tangent)

    @property
    def curvature(self) -> float:
        """有符号曲率（凹向下为负）。"""

        return -1.0 / self.radius

    def point_at_tangent(self, tangent: float) -> np.ndarray:
        """朴素的「圆心 + 半径方向」重建；供不变量审计使用，不作为求值主路径。"""

        return self.center + self.radius * np.array([-math.sin(tangent), math.cos(tangent)])

    def frame_at_tangent(self, tangent: float) -> tuple[np.ndarray, np.ndarray]:
        return (
            np.array([math.cos(tangent), math.sin(tangent)]),
            np.array([-math.sin(tangent), math.cos(tangent)]),
        )


@dataclass(frozen=True)
class CircularArcChain:
    """闭式的 ``N`` 段内切圆弧中弧线（弦长归一化为 1）。"""

    chord: float
    node_angles: np.ndarray
    spans: np.ndarray
    node_points: np.ndarray
    arcs: tuple[CircularArc, ...]
    cumulative_length: np.ndarray

    @property
    def total_length(self) -> float:
        return float(self.cumulative_length[-1])

    @property
    def arc_count(self) -> int:
        return len(self.arcs)

    @property
    def radius_array(self) -> np.ndarray:
        return np.array([arc.radius for arc in self.arcs])

    @property
    def center_array(self) -> np.ndarray:
        return np.array([arc.center for arc in self.arcs])

    def _segment_index(self, target: np.ndarray) -> np.ndarray:
        index = np.searchsorted(self.cumulative_length, target, side="right") - 1
        return np.clip(index, 0, self.arc_count - 1)

    def _tangent_angles_at(self, normalized_length: np.ndarray) -> np.ndarray:
        target = np.clip(normalized_length, 0.0, 1.0) * self.total_length
        index = self._segment_index(target)
        local = target - self.cumulative_length[index]
        radius = self.radius_array[index]
        start = self.node_angles[index]
        return start - local / radius

    def evaluate(self, normalized_length: np.ndarray) -> dict[str, np.ndarray]:
        """按归一化中弧线弧长精确求值：点、切线、法线、有符号曲率。

        点坐标用**弦向量**形式而不是 ``圆心 + r(−sin θ, cos θ)``：

        .. math::
            P(\\theta) = J_{i-1} + 2 r_i \\sin\\frac{\\phi_{i-1}-\\theta}{2}
                         \\left(\\cos\\frac{\\phi_{i-1}+\\theta}{2},\\;
                         \\sin\\frac{\\phi_{i-1}+\\theta}{2}\\right).

        两者数学上恒等，但相邻节点角间距极小时半径可达 ``1e12`` 以上，``圆心 + r(−sinθ,
        cosθ)`` 会发生灾难性抵消（绝对误差 ``r·ε``，实测达弦长的 1.6%），而弦向量形式把
        两个 O(1) 量的相减换成了半角正弦——``φ−θ`` 是两个邻近数的差，按 Sterbenz 引理
        精确，乘上半径后误差仍是有界的。旧实现因此在病态但被接受的输入上**静默**输出
        错误坐标，而 ``check_chain_invariants`` 的容差本就按 ``r`` 缩放、看不见这个问题。
        """

        parameter = np.atleast_1d(np.asarray(normalized_length, dtype=float))
        target = np.clip(parameter, 0.0, 1.0) * self.total_length
        index = self._segment_index(target)
        local = target - self.cumulative_length[index]
        radii = self.radius_array[index]
        start = self.node_angles[index]
        # ``(φ−θ)/2 = local/(2r)`` 直接由弧长参数给出，**不要**用 ``0.5*(φ−θ)`` 反推：
        # θ = φ − local/r 已经把 ``local/r`` 舍入掉了，再相减等于把该误差放大 r 倍。
        half_turn = 0.5 * local / radii
        tangent_angle = start - 2.0 * half_turn
        middle = start - half_turn
        chord = 2.0 * radii * np.sin(half_turn)
        points = self.node_points[index] + chord[:, None] * np.column_stack(
            [np.cos(middle), np.sin(middle)]
        )
        return {
            "points": points,
            "tangents": np.column_stack([np.cos(tangent_angle), np.sin(tangent_angle)]),
            "normals": np.column_stack([-np.sin(tangent_angle), np.cos(tangent_angle)]),
            "curvature": -1.0 / radii,
            "segment": index,
            "tangent_angle": tangent_angle,
        }

    def evaluate_at_x(self, x: np.ndarray) -> dict[str, np.ndarray]:
        """按弦向坐标精确求值（每段圆弧上 ``x`` 严格单调，故有闭式反解）。"""

        coordinate = np.atleast_1d(np.asarray(x, dtype=float))
        node_x = self.node_points[:, 0]
        index = np.clip(
            np.searchsorted(node_x, coordinate, side="right") - 1, 0, self.arc_count - 1
        )
        centers = self.center_array[index]
        radii = self.radius_array[index]
        sin_angle = np.clip((centers[:, 0] - coordinate) / radii, -1.0, 1.0)
        tangent_angle = np.arcsin(sin_angle)
        return {
            "y": centers[:, 1] + radii * np.cos(tangent_angle),
            "slope": np.tan(tangent_angle),
            "curvature": -1.0 / radii,
            "tangent_angle": tangent_angle,
            "segment": index,
        }

    def arc_length_at_x(self, x: float) -> float:
        """弦向坐标 ``x`` 处的累计弧长。"""

        node_x = self.node_points[:, 0]
        index = int(np.clip(np.searchsorted(node_x, x, side="right") - 1, 0, self.arc_count - 1))
        arc = self.arcs[index]
        tangent_angle = math.asin(float(np.clip((arc.center[0] - x) / arc.radius, -1.0, 1.0)))
        return float(
            self.cumulative_length[index] + arc.radius * (arc.start_tangent - tangent_angle)
        )

    def apex(self) -> tuple[float, float]:
        """最大弯度点的 ``(x, y)``（切线与弦线平行的位置）。"""

        best: tuple[float, float] | None = None
        for arc in self.arcs:
            if arc.start_tangent >= 0.0 >= arc.end_tangent:
                candidate = (float(arc.center[0]), float(arc.center[1] + arc.radius))
                if best is None or candidate[1] > best[1]:
                    best = candidate
        if best is None:
            ordered = self.node_points[np.argsort(self.node_points[:, 1])]
            best = (float(ordered[-1, 0]), float(ordered[-1, 1]))
        return best


def double_arc_first_span(phi0: float, phi2: float, middle: float) -> float:
    """双圆弧中弧线的第一段弦向跨度占比（闭式，端点上取极限值 0/1）。

    由 ``sum(delta)=1`` 与 ``sum(delta tan mu)=0`` 直接消元得到：

    .. math::  delta_1 = \\tan\\mu_2 / (\\tan\\mu_2 - \\tan\\mu_1).
    """

    mu1 = 0.5 * (phi0 + middle)
    mu2 = 0.5 * (middle + phi2)
    tan1 = math.tan(mu1)
    tan2 = math.tan(mu2)
    return tan2 / (tan2 - tan1)


def junction_station_bounds_deg(inlet_deg: float, outlet_deg: float) -> tuple[float, float]:
    """双圆弧切点切线角的**开区间**可行域（度），即 ``(-chi2, chi1)``。

    凸（凹向下）圆弧链要求节点切线角严格递减 ``phi_0 > phi_1 > phi_2``；而
    ``phi_0 = +chi1``、``phi_2 = -chi2``，所以 ``phi_1`` 必须落在 ``(-chi2, chi1)``
    内。这个区间是**必要条件**，但两端点都是退化极限（相应圆弧的平均切线角趋于 0、
    半径发散），因此实际可用的站位是开区间 ``(x_min, x_max)`` 的一个**真子集**，
    而不是整个 ``(0, 1)``。
    """

    return (-outlet_deg, inlet_deg)


def junction_station_range(inlet_deg: float, outlet_deg: float) -> tuple[float, float]:
    """双圆弧切点可达的弦向站位开区间 ``(x_min, x_max)``。

    端点值 ``delta_1(phi_0)``、``delta_1(phi_2)`` 会越出 ``(0, 1)``：``phi_1 -> chi1``
    时前段平均切线角趋于 0、前段退化为直线段，``delta_1`` 可以变成负数；``phi_1 -> -chi2``
    时后段同样退化，``delta_1`` 可以超过 1。可达站位因此是**两个端点值同时截断**的结果
    ``(max(0, min(a, b)), min(1, max(a, b)))``——写成 ``max(0, a)``／``min(1, b)`` 在非对称
    情形会给出空区间（例如 30/20 的 ``a = 0.806209``、``b = -0.178599``）。

    ``x_1(phi_1)`` 在 ``(-chi2, chi1)`` 上**并非处处**单调：实测
    ``sign(d delta_1/d phi_1) = -sign(cos(phi_1 + (chi1 - chi2)/2))``，当
    ``chi1 > 60 + chi2/3``（或对称地 ``chi2 > 60 + chi1/3``）时会出现一段上升支。
    但该上升支完全落在可达区间之外（``delta_1 < 0`` 或 ``delta_1 > 1``），在
    ``0 < delta_1 < 1`` 上仍严格递减，故本函数的截断结果与根的唯一性都不受影响。
    """

    phi0 = math.radians(inlet_deg)
    phi2 = math.radians(-outlet_deg)
    raw_lower = double_arc_first_span(phi0, phi2, phi2)
    raw_upper = double_arc_first_span(phi0, phi2, phi0)
    return (max(0.0, min(raw_lower, raw_upper)), min(1.0, max(raw_lower, raw_upper)))


def solve_junction_angle_for_station(
    inlet_deg: float,
    outlet_deg: float,
    station: float,
) -> float:
    """双圆弧：求使切点弦向站位等于 ``station`` 的切点切线角（度）。

    可行域 ``phi_1 ∈ (-chi2, chi1)`` 上 ``x_1(phi_1)`` 在可达区间 ``(x_min, x_max)``
    内严格单调（区间外的端点附近可能有一段上升支，见
    :func:`junction_station_range`），因此给定站位在可达区间内唯一可解；超出可达区间
    时抛出异常并给出真实边界。
    """

    if not 0.0 < station < 1.0:
        raise ValueError("切点弦向站位必须在 (0, 1) 内")
    phi0 = math.radians(inlet_deg)
    phi2 = math.radians(-outlet_deg)
    lower, upper = phi2, phi0

    def residual(middle: float) -> float:
        return double_arc_first_span(phi0, phi2, middle) - station

    low_value, high_value = residual(lower), residual(upper)
    if not low_value >= 0.0 >= high_value:
        x_min, x_max = junction_station_range(inlet_deg, outlet_deg)
        raise ValueError(
            f"切点弦向站位 {station:.6f} 超出双圆弧可达区间 "
            f"({x_min:.6f}, {x_max:.6f})（chi1={inlet_deg}°, chi2={outlet_deg}°）。"
            f"两端点是圆弧退化极限，无法取到。"
        )
    root = brentq(residual, lower, upper, xtol=5e-16, rtol=8.9e-16, maxiter=200)
    return math.degrees(root)


def solve_span_distribution(
    node_angles_rad: np.ndarray,
    interior_stations: tuple[float, ...] = (),
) -> np.ndarray:
    """解出各段弦向跨度占比 ``delta``（两条线性闭合条件）。"""

    angles = np.asarray(node_angles_rad, dtype=float)
    count = angles.size - 1
    if count < 1:
        raise ValueError("圆弧链至少需要一段圆弧")
    mu = 0.5 * (angles[:-1] + angles[1:])
    tan_mu = np.tan(mu)
    if not np.all(np.isfinite(tan_mu)):
        raise ValueError("节点切线角导致平均角的正切奇异")
    if count == 1:
        # 容差与 ``ArcMeanLineSpec`` 共用同一个常量：旧版模型用 1e-9 度、生成器用
        # 1e-12 弧度的正切判据，中间那条缝里模型说合法、生成器说非法。
        if abs(math.degrees(angles[0] + angles[1])) > SINGLE_ARC_ANGLE_TOLERANCE_DEG:
            raise ValueError("单圆弧只在两端切线角相等时闭合")
        return np.ones(1)

    fixed = count - 2
    if len(interior_stations) != fixed:
        raise ValueError("内节点站位数量必须等于 N-2")
    delta = np.zeros(count)
    stations = np.asarray(interior_stations, dtype=float)
    if fixed:
        delta[:fixed] = np.diff(np.concatenate([[0.0], stations]))
    sum_fixed = float(delta[:fixed].sum()) if fixed else 0.0
    moment_fixed = float((delta[:fixed] * tan_mu[:fixed]).sum()) if fixed else 0.0
    a = float(tan_mu[count - 2])
    b = float(tan_mu[count - 1])
    if abs(a - b) < 1e-15:
        raise ValueError("最后两段的平均切线角退化，无法闭合")
    remainder = 1.0 - sum_fixed
    # delta[-2] + delta[-1] = remainder;  a*delta[-2] + b*delta[-1] = -moment_fixed
    delta[count - 2] = (-moment_fixed - b * remainder) / (a - b)
    delta[count - 1] = remainder - delta[count - 2]
    if not np.all(np.isfinite(delta)) or np.any(delta <= 0.0):
        raise ValueError(
            "闭合解出现非正弦向跨度（节点角或站位超出可行域）: "
            f"delta={np.round(delta, 6).tolist()}"
        )
    return delta


def interior_station_bounds(
    inlet_metal_angle_deg: float,
    outlet_metal_angle_deg: float,
    interior_node_angles_deg: tuple[float, ...],
    interior_node_stations: tuple[float, ...],
) -> tuple[tuple[float, float], ...]:
    """多圆弧每个内节点弦向站位的**可达开区间**（其余站位固定在当前值）。

    推导
    ----
    前 ``N-2`` 段跨度由站位直接给出 ``delta_k = x_k - x_{k-1}``，末两段由两条线性闭合条件
    解出。把它们代入 ``delta > 0`` 后，可行域等价于关于 ``x`` 的三条**线性**不等式：

    .. math::
        S < 1,\\qquad P + b\\,(1 - S) < 0,\\qquad P + a\\,(1 - S) > 0,

    其中 ``S = \\sum_{k \\le N-2}\\delta_k``、``P = \\sum_{k \\le N-2}\\delta_k\\tan\\mu_k``、
    ``a = \\tan\\mu_{N-1}``、``b = \\tan\\mu_N``。节点角严格递减且都落在 ``(-90°, 90°)``
    内，故 ``\\tan`` 单调、``a > b``，上面第二个不等式的方向不依赖任何额外假设。

    单变量方向：``x_j`` 只通过 ``\\delta_j``（系数 ``+1``）与 ``\\delta_{j+1}``（系数
    ``-1``，仅当 ``j+1`` 仍是自由段时存在）进入，因此 ``S`` 对它的导数只有 ``0`` 或 ``1``
    两种取值，``P`` 的导数是 ``\\tan\\mu_j - \\tan\\mu_{j+1}`` 或 ``\\tan\\mu_j``。每条不等式
    于是给出一个闭式半轴，取交即得区间——不需要线性规划，也不需要扫描。

    区间端点由严格不等式给出，因此是**开**区间：取到端点意味着某段跨度恰好为零，
    半径发散，链退化。这与 :func:`junction_station_range` 的语义一致。

    当**其余**站位已经使链不可行时，每条区间都为空，此时返回 ``(nan, nan)``——调用方
    必须显式区分「区间很窄」与「根本没有可行值」，不能把空区间当成一个很窄的区间。
    """

    angles = np.radians(
        [inlet_metal_angle_deg, *interior_node_angles_deg, -outlet_metal_angle_deg]
    )
    count = angles.size - 1
    fixed = count - 2
    stations = tuple(float(value) for value in interior_node_stations)
    if fixed <= 0:
        return ()
    if len(stations) != fixed:
        raise ValueError(f"内节点站位数量必须等于 N-2 = {fixed}，收到 {len(stations)} 个")

    tan_mu = np.tan(0.5 * (angles[:-1] + angles[1:]))
    nodes = np.concatenate([[0.0], stations])
    spans = np.diff(nodes)
    total = float(spans.sum())
    moment = float((spans * tan_mu[:fixed]).sum())
    a = float(tan_mu[count - 2])
    b = float(tan_mu[count - 1])

    bounds: list[tuple[float, float]] = []
    for index in range(fixed):
        station = stations[index]
        has_next_span = index + 1 <= fixed - 1
        slope_total = 0.0 if has_next_span else 1.0
        slope_moment = float(tan_mu[index]) - (
            float(tan_mu[index + 1]) if has_next_span else 0.0
        )
        lower = stations[index - 1] if index > 0 else 0.0
        upper = stations[index + 1] if index + 1 < fixed else 1.0
        empty = False
        # 三条线性不等式 g(x) = g0 + grad * (x - station) < 0
        for g0, grad in (
            (total - 1.0, slope_total),
            (moment + b * (1.0 - total), slope_moment - b * slope_total),
            (-(moment + a * (1.0 - total)), -(slope_moment - a * slope_total)),
        ):
            if grad == 0.0:
                if g0 >= 0.0:
                    empty = True
                    break
            elif grad > 0.0:
                upper = min(upper, station - g0 / grad)
            else:
                lower = max(lower, station - g0 / grad)
        if empty or lower >= upper:
            bounds.append((float("nan"), float("nan")))
        else:
            bounds.append((float(lower), float(upper)))
    return tuple(bounds)


def build_arc_chain(
    spec: ArcMeanLineSpec,
    chord: float = 1.0,
    *,
    check_closure: bool = True,
) -> CircularArcChain:
    """由中弧线合同构造闭式圆弧链。"""

    if chord <= 0.0:
        raise ValueError("弦长必须为正")
    angles = np.radians(np.asarray(spec.resolved_node_angles_deg(), dtype=float))
    delta = solve_span_distribution(angles, spec.interior_node_stations)
    mu = 0.5 * (angles[:-1] + angles[1:])
    node_points = np.column_stack(
        [
            np.concatenate([[0.0], np.cumsum(delta)]) * chord,
            np.concatenate([[0.0], np.cumsum(delta * np.tan(mu))]) * chord,
        ]
    )

    arcs: list[CircularArc] = []
    for index in range(angles.size - 1):
        turn = angles[index] - angles[index + 1]
        if turn <= 0.0:
            # 两个节点角的弧度值塌缩成同一个数时 ``sin(turn/2) == 0``，半径公式除零。
            # 模型层已按弧度判严格递减，这里是兜底：把 ZeroDivisionError 换成契约异常。
            raise ValueError(
                f"第 {index + 1} 段圆弧的转角为 {math.degrees(turn):.3e}°，半径无法定义："
                f"相邻节点切线角在弧度域上不严格递减。"
            )
        radius = (
            float(delta[index]) * chord / (2.0 * math.sin(0.5 * turn) * math.cos(mu[index]))
        )
        start = node_points[index]
        center = start + radius * np.array([math.sin(angles[index]), -math.cos(angles[index])])
        arcs.append(
            CircularArc(
                center=center,
                radius=radius,
                start_tangent=float(angles[index]),
                end_tangent=float(angles[index + 1]),
                start_point=start,
            )
        )
    chain = CircularArcChain(
        chord=float(chord),
        node_angles=angles,
        spans=delta * chord,
        node_points=node_points,
        arcs=tuple(arcs),
        cumulative_length=np.concatenate([[0.0], np.cumsum([arc.length for arc in arcs])]),
    )
    if check_closure:
        check_chain_invariants(chain)
    return chain


def check_chain_invariants(chain: CircularArcChain) -> dict[str, float]:
    """验证圆弧链的闭合与节点相切不变量（超出门限即抛错）。

    容差按**几何尺度**缩放，而不是绝对常数。节点重建显式使用**朴素**形式
    ``center + r(-sin φ, cos φ)``（而不是 :attr:`CircularArc.end` 的稳定弦向量形式）：
    这一步审计的正是「圆心 + 半径方向」能否复现节点，改用稳定形式会让检查变成同义反复。
    其舍入误差量级是 ``r · ε``（``ε ≈ 2.2e-16``）；当相邻节点角间距极小时半径可达
    ``1e12`` 以上，此时绝对门限 ``1e-12`` 会被正常的浮点舍入击穿，把数学上完全合法的链
    误判为病态。用 ``max(1, r_max)`` 缩放后，判据与该重建方式的可表示精度一致。
    """

    rebuild = np.vstack(
        [chain.node_points[0], *[arc.point_at_tangent(arc.end_tangent) for arc in chain.arcs]]
    )
    node_error = float(np.max(np.abs(rebuild - chain.node_points)))
    tangency_error = max(
        (
            abs(arcs_next.start_tangent - arcs_previous.end_tangent)
            for arcs_previous, arcs_next in zip(chain.arcs, chain.arcs[1:], strict=False)
        ),
        default=0.0,
    )
    tail_y = float(abs(chain.node_points[-1, 1]))
    tail_x = float(abs(chain.node_points[-1, 0] - chain.chord))
    # 以最大半径为尺度；对常规叶型（r ~ 1）退化为原绝对门限，行为不变。
    scale = max(1.0, float(chain.radius_array.max()))
    node_tolerance = 1e-12 * scale
    chord_tolerance = 1e-12 * scale
    if (
        node_error > node_tolerance
        or tail_y > chord_tolerance
        or tail_x > chord_tolerance
        or tangency_error > 1e-15
    ):
        raise ArithmeticError(
            "圆弧链不变量失败: "
            f"node={node_error:.3e}/{node_tolerance:.3e} "
            f"tail_y={tail_y:.3e}/{chord_tolerance:.3e} "
            f"tail_x={tail_x:.3e}/{chord_tolerance:.3e} "
            f"tangency={tangency_error:.3e}"
        )
    return {
        "node_rebuild_error": node_error,
        "chord_closure_error": tail_y,
        "chord_length_error": tail_x,
        "node_tangency_error": float(tangency_error),
    }


# --------------------------------------------------------------------------------------
# thickness
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalThicknessLaw:
    """沿中弧线**法向**的对称半厚度 ``h(s)``，``s`` 为归一化中弧线弧长（弦长 1）。

    前段（``s <= s_t``）：``h = a1 q + a2 q^2 + a3 q^3 + a4 q^4``，``q = sqrt(s)``，``s``
    为**归一化**弧长。首项 ``a1 = sqrt(2 r_LE)`` 使前缘极限曲率半径恰为

    .. math::  R_{LE} = \\frac{a_1^2}{2 L} = \\frac{r_{LE}}{L},

    其中 ``L`` 是中弧线总弧长（弦长归一化为 1 时 ``L >= 1``）。``L = 1``（直线中弧线）
    才退化为 ``R_LE = r_LE``；这也是 ``geometry-v1`` 只在直线中弧线上才严格成立的原因。

    后段（``s > s_t``）：五次 Hermite，与前端在 ``s_t`` 处二阶连续，并显式满足
    ``h(s_t) = h_max``、``h'(s_t) = 0``、``h(1) = h_TE``、``h'(1) = -m``（``m > 0``）。

    尾缘半楔角的定义
    ----------------

    几何量是**两侧叶面在尾缘的切线夹角之半**（以弦线局部坐标为基准）。叶面
    ``S = M + h n`` 在归一化弧长下的切向量是

    .. math::
        \\frac{dS}{ds} = L(1 - h\\kappa)\\,T + h'\\,n,\\qquad
        \\frac{dP}{ds} = L(1 + h\\kappa)\\,T - h'\\,n,

    故 ``\\beta`` 由 :func:`trailing_edge_surface_angles_deg` 给出；``h'(1)`` 与
    ``\\beta`` 之间含弧长因子 ``L`` 与曲率项 ``h\\kappa``，由
    :func:`aft_end_slope_for_wedge` 精确反解，而不是任何简单比例。

    只有**直线**中弧线（``L = 1``、``\\chi_2 = 0``、``\\kappa = 0``）才退化为教科书
    恒等式：两侧切线分别是 ``(1, h')`` 与 ``(1, -h')``，夹角为
    ``2 \\arctan(-h')``，其一半即 ``\\arctan(-h')``，于是 ``h'(1) = -\\tan\\beta``
    ——这正是 ``geometry-v1`` 的 ``slope_te = -tan(radians(...))`` 约定，本内核在
    该退化条件下与它逐位一致。默认 DCA30/20 的 ``L = 1.0312``、``h\\kappa = -0.0017``，
    忽略这两个因子会把实现的楔角低估约 3%。
    """

    max_thickness_ratio: float
    max_thickness_station: float
    leading_edge_radius_ratio: float
    trailing_edge_thickness_ratio: float
    trailing_edge_half_wedge_deg: float
    aft_end_slope: float
    fore_coefficients: np.ndarray
    aft_coefficients: np.ndarray
    arc_length: float = 1.0

    @property
    def _mean_line_length(self) -> float:
        return self.arc_length

    @property
    def half_thickness_max(self) -> float:
        return 0.5 * self.max_thickness_ratio

    @property
    def half_thickness_te(self) -> float:
        return 0.5 * self.trailing_edge_thickness_ratio

    @property
    def aft_length(self) -> float:
        return 1.0 - self.max_thickness_station

    @property
    def mean_line_length(self) -> float:
        """中弧线总弧长 ``L``（弦长归一化为 1，故 ``L >= 1``）。

        站位参数 ``s`` 是**归一化弧长**，而尾缘楔角约定建立在**弦向坐标** ``x`` 上，
        二者的换算是 ``dx = L cos φ · ds``。所有对外报告曲率/楔角的地方都必须乘上
        这个因子，否则会把 ``dh/ds`` 当成 ``dh/dx``（默认 DCA 下相差 3.2%）。
        """

        return self._mean_line_length

    def __call__(self, station: np.ndarray) -> np.ndarray:
        s = np.atleast_1d(np.asarray(station, dtype=float))
        flat = np.clip(s, 0.0, 1.0)
        half = np.empty_like(flat)
        split = self.max_thickness_station

        fore = flat <= split
        q = np.sqrt(np.maximum(flat[fore], 0.0))
        a1, a2, a3, a4 = self.fore_coefficients
        half[fore] = a1 * q + a2 * q**2 + a3 * q**3 + a4 * q**4

        aft = flat > split
        delta = (flat[aft] - split) / self.aft_length
        half[aft] = self.aft_coefficients @ np.vstack([delta**index for index in range(6)])

        half[flat <= 0.0] = 0.0
        half[flat >= 1.0] = self.half_thickness_te
        return half

    def derivative(self, station: np.ndarray) -> np.ndarray:
        """``dh/ds``（``s = 0`` 处为 ``+inf``，符合 ``sqrt`` 前缘律）。"""

        s = np.atleast_1d(np.asarray(station, dtype=float))
        flat = np.clip(s, 0.0, 1.0)
        slope = np.empty_like(flat)
        split = self.max_thickness_station

        fore = flat <= split
        q = np.sqrt(np.maximum(flat[fore], 0.0))
        a1, a2, a3, a4 = self.fore_coefficients
        with np.errstate(divide="ignore", invalid="ignore"):
            dh_dq = a1 + 2 * a2 * q + 3 * a3 * q**2 + 4 * a4 * q**3
            slope[fore] = 0.5 * dh_dq / q

        aft = flat > split
        delta = (flat[aft] - split) / self.aft_length
        slope[aft] = _poly_derivative(self.aft_coefficients, delta) / self.aft_length

        slope[flat <= 0.0] = math.inf
        return slope

    def second_derivative(self, station: np.ndarray) -> np.ndarray:
        """``d2h/ds2``（``s -> 0`` 时发散，符合 ``sqrt`` 前缘律）。"""

        s = np.atleast_1d(np.asarray(station, dtype=float))
        flat = np.clip(s, 0.0, 1.0)
        second = np.empty_like(flat)
        split = self.max_thickness_station

        fore = flat <= split
        q = np.sqrt(np.maximum(flat[fore], 0.0))
        a1, a2, a3, a4 = self.fore_coefficients
        with np.errstate(divide="ignore", invalid="ignore"):
            d2h_dq2 = 2 * a2 + 6 * a3 * q + 12 * a4 * q**2
            dh_dq = a1 + 2 * a2 * q + 3 * a3 * q**2 + 4 * a4 * q**3
            second[fore] = (d2h_dq2 - dh_dq / q) / (4.0 * q**2)

        aft = flat > split
        delta = (flat[aft] - split) / self.aft_length
        second[aft] = _poly_second(self.aft_coefficients, delta) / self.aft_length**2

        second[flat <= 0.0] = -math.inf
        return second


def _hermite5_coefficients(
    y0: float, d0: float, d2_0: float, y1: float, d1: float, d2_1: float
) -> tuple[float, ...]:
    """归一化区间 ``[0, 1]`` 上的五次 Hermite 系数（低次幂在前），纯 Python 元组。

    闭式解，等价于对 ``[[1, 1, 1], [3, 4, 5], [6, 12, 20]]``（行列式为 2）求解，但不调用
    LAPACK。系数对全部六个边界数据**严格线性**，这正是「尾段入口二阶导数 ``Y`` 是唯一
    自由度、且 ``max h'`` 关于 ``Y`` 是凸函数」的代数基础。

    返回元组而不是 ``ndarray``：可行性搜索与楔角上界求解要在一次调用里求值上万次，
    而构造一个 6 元素 ``ndarray`` 的开销比整个多项式运算还大（实测热路径因此慢 5 倍）。
    """

    c0, c1, c2 = y0, d0, 0.5 * d2_0
    residual_0 = y1 - c0 - c1 - c2
    residual_1 = d1 - c1 - 2.0 * c2
    residual_2 = d2_1 - 2.0 * c2
    return (
        c0,
        c1,
        c2,
        10.0 * residual_0 - 4.0 * residual_1 + 0.5 * residual_2,
        -15.0 * residual_0 + 7.0 * residual_1 - residual_2,
        6.0 * residual_0 - 3.0 * residual_1 + 0.5 * residual_2,
    )


def _hermite5(
    y0: float, d0: float, d2_0: float, y1: float, d1: float, d2_1: float
) -> np.ndarray:
    """``_hermite5_coefficients`` 的 ``ndarray`` 形式（供厚度律存储与向量化求值使用）。"""

    return np.array(_hermite5_coefficients(y0, d0, d2_0, y1, d1, d2_1))


def _polyval(coefficients, x: float) -> float:
    """Horner 求值（低次幂在前），纯 Python，避免 numpy 的逐元素开销。"""

    result = 0.0
    for value in reversed(coefficients):
        result = result * x + value
    return result


def _cubic_real_roots(d: float, c: float, b: float, a: float) -> list[float]:
    """``a x³ + b x² + c x + d = 0`` 的全部实根（闭式）。

    先用 ``x = y − b/(3a)`` 降幂为 ``y³ + py + q = 0``，再按判别式
    ``Δ = (q/2)² + (p/3)³`` 分支：``Δ > 0`` 一个实根（Cardano），``Δ <= 0`` 三个实根
    （三角解法）。``Δ`` 的符号判据在重根附近会被舍入影响，但这里只用于定位导数驻点，
    漏掉一个重根不改变极值——极值同时是端点候选。

    ``numpy.roots`` 走伴随矩阵特征值，单次约 60 µs；本函数约 2 µs，而可行性搜索与
    楔角上界求解要调用它上万次，这是能不能把 ``arc limits`` 压进百毫秒量级的关键。
    """

    p = (3.0 * a * c - b * b) / (3.0 * a * a)
    q = (2.0 * b**3 - 9.0 * a * b * c + 27.0 * a * a * d) / (27.0 * a**3)
    shift = b / (3.0 * a)
    discriminant = (q / 2.0) ** 2 + (p / 3.0) ** 3
    if discriminant > 0.0:
        root = math.sqrt(discriminant)
        first = -q / 2.0 + root
        second = -q / 2.0 - root
        return [
            math.copysign(abs(first) ** (1.0 / 3.0), first)
            + math.copysign(abs(second) ** (1.0 / 3.0), second)
            - shift
        ]
    if p == 0.0:
        return [-shift]
    radius = 2.0 * math.sqrt(-p / 3.0)
    argument = (3.0 * q) / (2.0 * p) * math.sqrt(-3.0 / p)
    theta = math.acos(min(1.0, max(-1.0, argument))) / 3.0
    return [
        radius * math.cos(theta - 2.0 * math.pi * index / 3.0) - shift for index in range(3)
    ]


def _real_roots_within(coefficients, lower: float, upper: float) -> list[float]:
    """低次幂在前的多项式在 ``[lower, upper]`` 内的实根（次数 ``<= 3`` 走闭式）。"""

    values = [float(value) for value in coefficients]
    while len(values) > 1 and values[-1] == 0.0:
        values.pop()
    degree = len(values) - 1
    if degree < 1:
        return []
    if degree == 1:
        roots = [-values[0] / values[1]]
    elif degree == 2:
        constant, linear, quadratic = values[0], values[1], values[2]
        discriminant = linear * linear - 4.0 * quadratic * constant
        if discriminant < 0.0:
            return []
        square = math.sqrt(discriminant)
        roots = [
            (-linear - square) / (2.0 * quadratic),
            (-linear + square) / (2.0 * quadratic),
        ]
    elif degree == 3:
        roots = _cubic_real_roots(values[0], values[1], values[2], values[3])
    else:  # pragma: no cover - 本模块的驻点多项式最高三次
        trimmed = np.trim_zeros(np.asarray(values[::-1], dtype=float), trim="f")
        roots = [
            float(root.real)
            for root in np.roots(trimmed)
            if abs(root.imag) <= 1e-9 * max(1.0, abs(root.real))
        ]
    return [root for root in roots if lower - 1e-12 <= root <= upper + 1e-12]


def _polynomial_extreme(coefficients, lower: float, upper: float, *, largest: bool) -> float:
    """**导数**在闭区间 ``[lower, upper]`` 上的精确极值。

    候选点只有两类：区间端点，以及二阶导数的实根（区间内的驻点）。求根走
    :func:`_real_roots_within` 的闭式，因此既没有网格误差（旧实现的粗网格判据正是被
    这一点击穿的），也没有伴随矩阵的开销。全程纯 Python 标量运算：调用方在热路径上
    每秒会走几十万次，构造临时 ``ndarray`` 的开销比多项式运算本身还大。
    """

    derivative = tuple(
        index * float(coefficients[index]) for index in range(1, len(coefficients))
    )
    stationarity = tuple(
        index * derivative[index] for index in range(1, len(derivative))
    )
    candidates = [float(lower), float(upper)]
    candidates.extend(_real_roots_within(stationarity, lower, upper))
    values = [_polyval(derivative, point) for point in candidates]
    return max(values) if largest else min(values)


def _aft_slope_max_ds(
    hmax: float, hte: float, length: float, magnitude: float, entry_second: float
) -> float:
    """尾段 ``dh/ds`` 的最大值（``s`` 为归一化弧长，``s > s_t``）。

    尾段是尾段坐标 ``delta`` 上的五次多项式，故 ``dh/ddelta`` 是四次多项式；这里取它的
    精确最大值再除以 ``length`` 换算到 ``s``。返回值关于 ``entry_second`` 是**凸函数**
    （Hermite 系数对边界数据线性，而最大值是仿射函数族的上确界），这正是
    :func:`_normal_thickness_curvature_search` 用三分搜索求最小值的依据。
    """

    coefficients = _hermite5_coefficients(
        hmax, 0.0, entry_second * length**2, hte, -magnitude * length, 0.0
    )
    return _polynomial_extreme(coefficients, 0.0, 1.0, largest=True) / length


def _monotone_slope_tolerance(hmax: float) -> float:
    """单调性判据的斜率容差（``dh/ds`` 单位）。

    ``h`` 的尺度是 ``h_max``、``s`` 的定义域是 ``[0, 1]``，故 ``1e-9 · h_max`` 对应整段
    厚度上约 ``6e-10 · h_max`` 的伪回升——远低于任何几何意义；同时远高于双精度舍入：
    Hermite 系数的尺度是 ``h_max / length**5``（默认合同约 ``0.56``），舍入误差量级
    ``1e-16``，比该容差小五个数量级。

    **这个函数是判据的唯一来源**：可行性搜索、楔角上界求解与 :func:`_verify_law`
    必须共用它，否则就会出现「报出的上界生成器实现不了」这类不一致（旧实现的真实缺陷）。
    """

    return 1e-9 * hmax


def _poly_derivative(coefficients: np.ndarray, x: np.ndarray) -> np.ndarray:
    return sum(index * coefficients[index] * np.power(x, index - 1) for index in range(1, 6))


def _poly_second(coefficients: np.ndarray, x: np.ndarray) -> np.ndarray:
    return sum(
        index * (index - 1) * coefficients[index] * np.power(x, index - 2)
        for index in range(2, 6)
    )


def _quintic_hermite_coefficients(
    y0: float, dy0: float, d2y0: float, y1: float, dy1: float, d2y1: float
) -> np.ndarray:
    """历史别名：保留 ``geometry-v1`` 的签名以兼容既有调用。"""

    return _hermite5(y0, dy0, d2y0, y1, dy1, d2y1)


def normal_thickness_at_spec_half(
    spec: ArcProfileSpec, station: np.ndarray
) -> np.ndarray:
    """便捷入口：按 ``spec`` 构造厚度律并求值（自动使用正确的尾缘斜率约定）。"""

    return build_thickness_law(spec)(station)


def trailing_edge_surface_angles_deg(
    aft_slope: float,
    arc_length: float,
    outlet_deg: float,
    half_thickness_te: float,
    terminal_curvature: float,
) -> tuple[float, float]:
    """尾缘两侧叶面的**绝对切线角**（度），以弦线为基准，吸力面在前、压力面在后。

    半楔角不是这两个返回值本身，而是它们的差的绝对值之半
    （``0.5 * |pressure - suction|``，即 :func:`half_wedge_from_slope_deg`）。

    叶面在归一化弧长下的切向量是

    .. math::
        \\frac{dS}{ds} = L(1 - h\\kappa)\\,T + h'\\,n,\\qquad
        \\frac{dP}{ds} = L(1 + h\\kappa)\\,T - h'\\,n .

    中弧线在尾缘的切线角是 ``−χ₂``，因此两侧切线角为

    .. math::
        \\alpha_S = -\\chi_2 - \\arctan\\frac{|h'|}{L(1-h\\kappa)},\\qquad
        \\alpha_P = -\\chi_2 + \\arctan\\frac{|h'|}{L(1+h\\kappa)} .

    **曲率项 ``hκ`` 不可忽略**：小半径尾段的 ``|hκ|`` 可达 0.1 量级，它让两侧楔角
    不再对称，也使 ``β`` 与 ``h'`` 的关系不再是简单的 ``tan`` 比例。
    """

    magnitude = -aft_slope
    suction = -outlet_deg - math.degrees(
        math.atan2(magnitude, arc_length * (1.0 - half_thickness_te * terminal_curvature))
    )
    pressure = -outlet_deg + math.degrees(
        math.atan2(magnitude, arc_length * (1.0 + half_thickness_te * terminal_curvature))
    )
    return suction, pressure


def half_wedge_from_slope_deg(
    aft_slope: float,
    arc_length: float,
    outlet_deg: float,
    half_thickness_te: float,
    terminal_curvature: float,
) -> float:
    """由 ``dh/ds`` 算出**实际**尾缘半楔角（两侧平均，度）。"""

    suction, pressure = trailing_edge_surface_angles_deg(
        aft_slope, arc_length, outlet_deg, half_thickness_te, terminal_curvature
    )
    return 0.5 * (pressure - suction)


def aft_end_slope_for_wedge(
    half_wedge_deg: float,
    arc_length: float = 1.0,
    outlet_deg: float = 0.0,
    half_thickness_te: float = 0.0,
    terminal_curvature: float = 0.0,
) -> float:
    """由尾缘半楔角反解厚度律的末端斜率 ``dh/ds``（``s`` 为归一化弧长）。

    楔角由两侧叶面切线的夹角定义（见 :func:`trailing_edge_surface_angles_deg`）。
    该关系对 ``|h'|`` 严格单调，因此用二分反解，而不是套用任何近似比例。

    几何装配时必须传入真实的 ``arc_length``、``outlet_deg``、``half_thickness_te``
    与 ``terminal_curvature``，否则实现的楔角会系统性偏小。
    """

    if not 0.0 < half_wedge_deg < 89.0:
        raise ThicknessLawInfeasible("尾缘半楔角必须位于 (0, 89) 度")

    def residual(magnitude: float) -> float:
        return (
            half_wedge_from_slope_deg(
                -magnitude, arc_length, outlet_deg, half_thickness_te, terminal_curvature
            )
            - half_wedge_deg
        )

    low, high = 1e-12, 1e6
    low_value, high_value = residual(low), residual(high)
    # β 关于斜率幅值 m 严格单调**递减**：m -> 0 时 β -> 0°，m -> inf 时 β -> 90°。
    # 因此 residual = β(m) - target 在 low 端为负、high 端为正。
    if not low_value < 0.0 < high_value:
        raise ThicknessLawInfeasible(
            f"尾缘半楔角 {half_wedge_deg}° 无法由厚度斜率实现："
            f"β(m->0)={low_value + half_wedge_deg:.6f}°, "
            f"β(m->inf)={high_value + half_wedge_deg:.6f}°"
        )
    root = brentq(residual, low, high, xtol=5e-16, rtol=8.9e-16, maxiter=200)
    return -root


def thickness_end_slope_to_chordwise(
    law: NormalThicknessLaw, outlet_deg: float
) -> float:
    """把厚度律的 ``dh/ds`` 末端斜率换算成弦向 ``dh/dx``。

    仅用于报告；楔角本身由 :func:`trailing_edge_half_wedges_deg` 按真实叶面切线
    夹角计算，因为曲率项 ``hκ`` 会让它与 ``arctan(-dh/dx)`` 不再相等。
    """

    return law.aft_end_slope / (law.mean_line_length * math.cos(math.radians(outlet_deg)))


def trailing_edge_half_wedges_deg(
    law: NormalThicknessLaw,
    outlet_deg: float = 0.0,
    terminal_curvature: float = 0.0,
) -> tuple[float, float]:
    """尾缘两侧叶面的**绝对切线角**（度），按弦线局部坐标定义，吸力面在前。

    注意：返回值**不是**两个半楔角，而是两条叶面切线相对弦线的绝对角度；两侧半楔角
    是二者之差的一半（``0.5 * |pressure - suction|``，用 :func:`half_wedge_from_slope_deg`
    直接取）。直接把厚度律的 ``dh/ds`` 与中弧线尾缘曲率代入
    :func:`trailing_edge_surface_angles_deg` 得到这两个角，因此任何单位换算或曲率项
    遗漏都会在这里暴露，而不是被同义反复的检查掩盖。
    """

    return trailing_edge_surface_angles_deg(
        law.aft_end_slope,
        law.mean_line_length,
        outlet_deg,
        law.half_thickness_te,
        terminal_curvature,
    )


class ThicknessLawInfeasible(ArithmeticError):
    """请求的厚度合同无法实现为单调、二阶连续的对称法向厚度律。"""


def build_thickness_law(
    spec: ArcProfileSpec,
    aft_end_slope: float | None = None,
    *,
    arc_length: float = 1.0,
    outlet_metal_angle_deg: float = 0.0,
    terminal_curvature: float = 0.0,
) -> NormalThicknessLaw:
    """构造两段式对称法向厚度律。

    ``aft_end_slope`` 缺省时按 :func:`aft_end_slope_for_wedge` 从楔角精确反解：
    楔角由**两侧叶面切线的夹角**定义，含曲率项 ``hκ``，因此在几何装配路径上
    必须传入真实的 ``arc_length``、``outlet_metal_angle_deg`` 与
    ``terminal_curvature``。构造完成后会验证单调性、峰值位置与**实现楔角**，
    任何违反都抛 :class:`ThicknessLawInfeasible`。
    """

    split = spec.max_thickness_station
    length = 1.0 - split
    hmax = 0.5 * spec.max_thickness_ratio
    hte = 0.5 * spec.trailing_edge_thickness_ratio
    drop = hmax - hte
    if drop <= 0.0:
        raise ThicknessLawInfeasible("尾段厚度落差必须为正")

    if aft_end_slope is None:
        aft_end_slope = aft_end_slope_for_wedge(
            spec.trailing_edge_half_wedge_deg,
            arc_length,
            outlet_metal_angle_deg,
            hte,
            terminal_curvature,
        )
    if aft_end_slope >= 0.0:
        raise ThicknessLawInfeasible("尾缘厚度斜率必须为负")

    entry_second = _aft_entry_second(
        hmax,
        hte,
        length,
        aft_end_slope,
        arc_length=arc_length,
        outlet_metal_angle_deg=outlet_metal_angle_deg,
        terminal_curvature=terminal_curvature,
        split=split,
        leading_edge_radius_ratio=spec.leading_edge_radius_ratio,
    )
    aft = _hermite5(hmax, 0.0, entry_second * length**2, hte, aft_end_slope * length, 0.0)
    fore_coefficients = _fore_coefficients(spec, entry_second)
    law = NormalThicknessLaw(
        max_thickness_ratio=spec.max_thickness_ratio,
        max_thickness_station=split,
        leading_edge_radius_ratio=spec.leading_edge_radius_ratio,
        trailing_edge_thickness_ratio=spec.trailing_edge_thickness_ratio,
        trailing_edge_half_wedge_deg=spec.trailing_edge_half_wedge_deg,
        aft_end_slope=aft_end_slope,
        fore_coefficients=fore_coefficients,
        aft_coefficients=aft,
        arc_length=arc_length,
    )
    _verify_law(
        law,
        outlet_metal_angle_deg=outlet_metal_angle_deg,
        terminal_curvature=terminal_curvature,
    )
    return law


def _aft_entry_second(
    hmax: float,
    hte: float,
    length: float,
    slope: float,
    *,
    arc_length: float = 1.0,
    outlet_metal_angle_deg: float = 0.0,
    terminal_curvature: float = 0.0,
    split: float | None = None,
    leading_edge_radius_ratio: float | None = None,
) -> float:
    """尾段入口 ``d2h/ds2``（在归一化尾段坐标下）。

    这是尾段唯一可用的形状自由度：``h(1)``、``h'(1)``、``h'(s_t)`` 都已被合同钉死，
    只剩入口二阶导数可以把曲线压成单调下降。可行集是**有界区间**（``|Y|`` 太小会
    回升、太大又会在中间塌陷），由 :func:`_normal_thickness_curvature_search`
    求该区间内最平滑的解。不可行时抛出带可达上界的异常。
    """

    magnitude = -slope
    candidate = _normal_thickness_curvature_search(
        hmax,
        hte,
        length,
        magnitude,
        split=split,
        leading_edge_radius_ratio=leading_edge_radius_ratio,
    )
    if candidate is None:
        ceiling = maximum_feasible_half_wedge_deg(
            2.0 * hmax,
            1.0 - length,
            2.0 * hte,
            arc_length=arc_length,
            outlet_metal_angle_deg=outlet_metal_angle_deg,
            terminal_curvature=terminal_curvature,
            leading_edge_radius_ratio=leading_edge_radius_ratio,
        )
        raise ThicknessLawInfeasible(
            f"该厚度合同无法实现请求的尾缘楔角：尾段长度 {length:.4f} 上厚度落差只有 "
            f"{hmax - hte:.6f}，单条二阶连续的单调曲线压不出这么尖的尾缘。"
            f"该合同下可行的最大半楔角约为 {ceiling:.4f}°——请增大 max_thickness_ratio、"
            f"减小 trailing_edge_thickness_ratio、前移 max_thickness_station，"
            f"或降低 trailing_edge_half_wedge_deg。"
        )
    return candidate


_CURVATURE_SEARCH_LIMIT = 200.0
_CURVATURE_BRACKET_MARGIN = 1.0e4
_CURVATURE_BRACKET_EXPANSIONS = 3
# 三分搜索每步把区间缩到 2/3：(2/3)^80 ≈ 8e-15，相对一个 ~1e3 的区间即 1e-11，
# 而最小值点附近的函数误差是二阶量（~1e-22），远小于斜率容差 5e-11。
_CURVATURE_TERNARY_STEPS = 80
_CURVATURE_BISECTION_STEPS = 80
_CEILING_BISECTION_STEPS = 60
# 上界要能被用户直接拿去用：「β ≤ 报出的上界」必须真的可行。二分给出的可行幅值
# 精度约 1e-17（相对），但用户把上界再喂回 ``aft_end_slope_for_wedge`` 反解时会有
# 约 5e-16 的往返误差，足以把结果推到可行域之外一个「最后一位」。这里内缩 1e-9 度
# ——比往返误差大五个数量级，比任何可观测的几何量小七个数量级。
_CEILING_SAFETY_MARGIN_DEG = 1e-9
_CURVATURE_FLOOR = 1e-12


def _curvature_search_bracket(hmax: float, hte: float, length: float) -> float:
    """可行 ``|Y|`` 的搜索上界（正数，调用方取负）。

    ``Y`` 是 ``d²h/ds²``，量纲是 ``h / s²``，因此自然尺度是「厚度落差 / 尾段长度²」。
    实测可行 ``|Y|`` 最多是该尺度的几十倍（默认合同：尺度 ``0.122``，可行上界 ``3.8``），
    这里取 ``1e4`` 倍再与 ``_CURVATURE_SEARCH_LIMIT`` 取大，留出两个数量级以上的余量。
    """

    drop = hmax - hte
    natural = drop / (length * length) if length > 0.0 else _CURVATURE_SEARCH_LIMIT
    return max(_CURVATURE_SEARCH_LIMIT, _CURVATURE_BRACKET_MARGIN * natural)


def _ternary_minimise(function, lower: float, upper: float, steps: int) -> float:
    """凸函数在 ``[lower, upper]`` 上的三分搜索最小值点。

    被最小化的 ``max_delta h'(delta; Y)`` 是仿射函数族的上确界，因而关于 ``Y`` **凸**；
    凸函数是单峰的，三分搜索因此收敛到全局最小值点，不需要任何初值猜测。
    """

    low, high = lower, upper
    for _ in range(steps):
        left = low + (high - low) / 3.0
        right = high - (high - low) / 3.0
        if function(left) < function(right):
            high = right
        else:
            low = left
    return 0.5 * (low + high)


def _normal_thickness_curvature_search(
    hmax: float,
    hte: float,
    length: float,
    magnitude: float,
    *,
    split: float | None = None,
    leading_edge_radius_ratio: float | None = None,
) -> float | None:
    """为给定末端斜率幅值 ``magnitude`` 搜索可行的入口二阶导数 ``Y``。

    判据与 :func:`_verify_law` **完全一致**（同一个 :func:`_monotone_slope_tolerance`
    与同一组解析极值函数）。这是本函数存在的全部意义：只要它返回一个值，构造出来的
    厚度律就一定通过门禁；只要它返回 ``None``，就真的不存在可行解。

    旧实现用「601 点 ``Y`` 粗扫 + 2001 点 ``delta`` 采样 + 值差分容差」做判据，与门禁
    相差约三个数量级：在默认合同下它会接受 ``max dh/ds = 2.3e-7`` 的解，而门禁只允许
    ``5e-11``。后果是报出的楔角上界比生成器真正能实现的上界高，并且上界之下就被拒绝
    ——这正是「上界不可信」的真实缺陷。

    搜索目标必须是**合并违反量**，而不是尾段违反量：尾段约束 ``max dh/ds <= tol`` 与
    前段约束 ``min dh/ds >= -tol`` 是两个不同的 ``Y`` 的函数，前段更严时「尾段最优点」
    本身就不可行。只按尾段最小值点做二分，会在可行区间落在另一侧时直接返回一个不可行
    的解（实测：``t/c=0.02``、``s_t=0.20``、``t_TE/c=5e-4``、``β=0.6°`` 的合同会被误拒）。

    合并违反量仍然凸：``max_δ h'(δ; Y)`` 关于 ``Y`` 凸（仿射函数族的上确界），而前段
    ``min_q N(q;Y)/(2q)`` 关于 ``Y`` **凹**（一族仿射函数的下确界），故
    ``violation = max(尾段违反量, 前段违反量)`` 是两个凸函数的最大值，仍是凸函数。
    于是下水平集 ``{violation <= 0}`` 是区间，三分搜索求最小值、二分收上端点都成立。
    """

    tolerance = _monotone_slope_tolerance(hmax)

    def aft_slope(entry_second: float) -> float:
        return _aft_slope_max_ds(hmax, hte, length, magnitude, entry_second)

    def fore_slope(entry_second: float) -> float:
        # 未提供前段参数时返回 ``+inf``，使 ``-fore_slope - tolerance`` 变成 ``-inf``，
        # 即「前段永不违反」。返回 ``-inf`` 会让合并违反量恒为 ``+inf``，把所有合同都判负。
        if split is None or leading_edge_radius_ratio is None:
            return math.inf
        return _fore_slope_min_ds(hmax, split, leading_edge_radius_ratio, entry_second)

    def violation(entry_second: float) -> float:
        return max(aft_slope(entry_second) - tolerance, -fore_slope(entry_second) - tolerance)

    def feasible(entry_second: float) -> bool:
        return violation(entry_second) <= 0.0

    floor = -_CURVATURE_FLOOR
    lower = -_curvature_search_bracket(hmax, hte, length)
    minimiser = _ternary_minimise(violation, lower, floor, _CURVATURE_TERNARY_STEPS)
    # 只有当最小值点仍钉在左边界、而且**还没找到可行解**时才需要扩大搜索上界：
    # 说明真正的极小值可能在区间之外。已经可行时不必扩，下水平集的上端点由后面的
    # 二分照常给出。
    for _ in range(_CURVATURE_BRACKET_EXPANSIONS):
        if feasible(minimiser):
            break
        if (minimiser - lower) > 1e-6 * (floor - lower):
            break
        lower *= 1.0e6
        minimiser = _ternary_minimise(violation, lower, floor, _CURVATURE_TERNARY_STEPS)
    if not feasible(minimiser):
        return None
    if feasible(floor):
        return floor
    # 最平滑的可行解是可行区间靠近 0 的那个端点，二分把它收细。
    low, high = minimiser, floor
    for _ in range(_CURVATURE_BISECTION_STEPS):
        middle = 0.5 * (low + high)
        if feasible(middle):
            low = middle
        else:
            high = middle
    return low


def maximum_feasible_half_wedge_deg(
    max_thickness_ratio: float,
    max_thickness_station: float,
    trailing_edge_thickness_ratio: float,
    *,
    arc_length: float = 1.0,
    outlet_metal_angle_deg: float = 0.0,
    terminal_curvature: float = 0.0,
    leading_edge_radius_ratio: float | None = None,
) -> float:
    """给定厚度合同下可以实现的**最大**尾缘半楔角（度）。

    可行性判据建立在 ``dh/ds``（``s`` 为归一化弧长）上，而角度按两侧叶面切线的
    真实夹角报告，二者之间含弧长因子与曲率项。因此先在斜率域二分出可行上界，
    再用 :func:`half_wedge_from_slope_deg` 精确换算——直接对角度二分会把上界报错。

    ``leading_edge_radius_ratio``
    -----------------------------

    给出时把**前段**单调性一并纳入判据，报出的上界因此与 ``generate_arc_profile`` 的
    真实接受边界一致——这是 ``arc_feasibility_report``（Web / CLI 对外路径）使用的口径。

    省略时（``None``）只判**尾段**，返回值是一个**仅对尾段成立的宽松上界**：实测它会
    高于真实边界（例如 ``t/c=0.05``、``s_t=0.55``、``t_TE/c=5e-4``、``r_LE=0.00125``
    时给出 ``10.2049°``，而真实边界是 ``9.7397°``）。旧版 docstring 声称「实测该前段项
    在可行解上从不绑定」，**这是错的**：前段约束在不少合同上就是绑定约束。

    ``maximum_feasible_half_wedge_deg(0.10, 0.40, 0.006) == 14.806`` 这类省略参数的
    调用只用于记录 ``L = 1``、``κ = 0`` 的退化值，不应作为对外承诺。
    """

    hmax = 0.5 * max_thickness_ratio
    hte = 0.5 * trailing_edge_thickness_ratio
    length = 1.0 - max_thickness_station

    def feasible(magnitude: float) -> bool:
        return (
            _normal_thickness_curvature_search(
                hmax,
                hte,
                length,
                magnitude,
                split=max_thickness_station if leading_edge_radius_ratio is not None else None,
                leading_edge_radius_ratio=leading_edge_radius_ratio,
            )
            is not None
        )

    def to_degrees(magnitude: float) -> float:
        return half_wedge_from_slope_deg(
            -magnitude, arc_length, outlet_metal_angle_deg, hte, terminal_curvature
        )

    # 尾段「斜率幅值 × 尾段长度 ≲ 3 × 厚度落差」是量级约束，故上界取 100 倍余量。
    # 从 [0, 1e6] 起步会把 60 次二分浪费在无意义的量级上；收窄区间让同样的迭代次数
    # 换到 1e-18 的相对精度。仍然显式检查上界本身可行的情况，不假设约束成立。
    upper = max(1e-9, 100.0 * (hmax - hte) / length) if length > 0.0 else 1e6
    if feasible(upper):
        upper = 1e6
        if feasible(upper):
            return to_degrees(upper)
    low, high = 0.0, upper
    if not feasible(low):
        return 0.0
    for _ in range(_CEILING_BISECTION_STEPS):
        middle = 0.5 * (low + high)
        if feasible(middle):
            low = middle
        else:
            high = middle
    return max(0.0, to_degrees(low) - _CEILING_SAFETY_MARGIN_DEG)


def _fore_coefficients(spec: ArcProfileSpec, fore_second_value: float) -> np.ndarray:
    """前段四次多项式系数（``q = sqrt(s)`` 的幂次）。"""

    return _fore_coefficients_from_values(
        max_thickness_ratio=spec.max_thickness_ratio,
        max_thickness_station=spec.max_thickness_station,
        leading_edge_radius_ratio=spec.leading_edge_radius_ratio,
        fore_second_value=fore_second_value,
    )


def _fore_coefficients_from_values(
    *,
    max_thickness_ratio: float,
    max_thickness_station: float,
    leading_edge_radius_ratio: float,
    fore_second_value: float,
) -> np.ndarray:
    """前段四次多项式系数（``q = sqrt(s)`` 的幂次），由标量参数直接给出。

    四个条件：``h(s_t) = h_max``、``dh/dq(s_t) = 0``（等价于 ``dh/ds(s_t) = 0``）、
    ``d²h/ds²(s_t) = fore_second_value``，以及 ``h(0) = 0``（由 ``q`` 的零次项为零自动
    满足）。``a₁ = √(2 r_LE)`` 由前缘半径合同直接钉死，不参与求解。

    3×3 系统有闭式解。记 ``q = √s_t``、``A = (h_max − a₁q)/q²``、``B = −a₁/q``、
    ``C = 4q²·fore_second_value``（第三行右端项，**必须带 ``q²`` 因子**），则

    .. math::
        a_2 = 6A - 3B + C/2,\\qquad a_3 = (5B - 8A - C)/q,\\qquad
        a_4 = (C - 4B + 6A)/(2q^2).

    热路径（可行性搜索）每秒调用上万次，闭式比 ``numpy.linalg.solve`` 快一个量级。
    """

    q_t = math.sqrt(max_thickness_station)
    a1 = math.sqrt(2.0 * leading_edge_radius_ratio)
    hmax = 0.5 * max_thickness_ratio
    coefficient_a = (hmax - a1 * q_t) / (q_t * q_t)
    coefficient_b = -a1 / q_t
    coefficient_c = 4.0 * q_t * q_t * fore_second_value
    a2 = 6.0 * coefficient_a - 3.0 * coefficient_b + 0.5 * coefficient_c
    a3 = (5.0 * coefficient_b - 8.0 * coefficient_a - coefficient_c) / q_t
    a4 = (coefficient_c - 4.0 * coefficient_b + 6.0 * coefficient_a) / (2.0 * q_t * q_t)
    return np.array([a1, a2, a3, a4])


def _fore_coefficient_tuple(
    max_thickness_ratio: float,
    max_thickness_station: float,
    leading_edge_radius_ratio: float,
    fore_second_value: float,
) -> tuple[float, float, float, float]:
    """``_fore_coefficients_from_values`` 的纯 Python 元组形式（热路径用）。"""

    q_t = math.sqrt(max_thickness_station)
    a1 = math.sqrt(2.0 * leading_edge_radius_ratio)
    hmax = 0.5 * max_thickness_ratio
    coefficient_a = (hmax - a1 * q_t) / (q_t * q_t)
    coefficient_b = -a1 / q_t
    coefficient_c = 4.0 * q_t * q_t * fore_second_value
    a2 = 6.0 * coefficient_a - 3.0 * coefficient_b + 0.5 * coefficient_c
    a3 = (5.0 * coefficient_b - 8.0 * coefficient_a - coefficient_c) / q_t
    a4 = (coefficient_c - 4.0 * coefficient_b + 6.0 * coefficient_a) / (2.0 * q_t * q_t)
    return (a1, a2, a3, a4)


def _fore_slope_min_from_coefficients(coefficients, split: float) -> float:
    """前段系数给定时 ``dh/ds`` 在 ``(0, s_t]`` 上的**精确**最小值。

    ``h = a₁q + a₂q² + a₃q³ + a₄q⁴``（``q = √s``）给出
    ``dh/ds = N(q) / (2q)``，``N(q) = a₁ + 2a₂q + 3a₃q² + 4a₄q³``。``q → 0⁺`` 时它发散到
    ``+∞``（``a₁ > 0``），因此最小值只可能落在 ``q = √s_t`` 或 ``qN'(q) − N(q) = 0``
    的驻点上——后者是一个三次多项式，同样解析求根，没有采样误差。
    """

    a1, a2, a3, a4 = (float(value) for value in coefficients)
    numerator = (a1, 2.0 * a2, 3.0 * a3, 4.0 * a4)
    q_t = math.sqrt(split)
    # q N'(q) - N(q) = sum_k (k - 1) n_k q^k
    stationarity = tuple(
        (index - 1) * numerator[index] for index in range(len(numerator))
    )
    candidates = [q_t]
    candidates.extend(
        root for root in _real_roots_within(stationarity, 0.0, q_t) if root > 1e-300
    )
    return min(_polyval(numerator, point) / (2.0 * point) for point in candidates)


def _fore_slope_min_ds(
    hmax: float,
    split: float,
    leading_edge_radius_ratio: float,
    entry_second: float,
) -> float:
    """由候选入口二阶导数 ``entry_second`` 构造前段系数后取精确最小斜率。"""

    return _fore_slope_min_from_coefficients(
        _fore_coefficient_tuple(2.0 * hmax, split, leading_edge_radius_ratio, entry_second),
        split,
    )


def _verify_law(
    law: NormalThicknessLaw,
    samples: int = 2001,
    *,
    outlet_metal_angle_deg: float = 0.0,
    terminal_curvature: float = 0.0,
) -> None:
    """显式验证厚度律的全部合同；任何违反都抛错，不静默修正。

    单调性判据是**解析**的：尾段取 ``dh/ddelta`` 四次多项式在 ``[0, 1]`` 上的精确最大值
    （驻点 + 端点），前段取 ``N(q)/(2q)`` 在 ``(0, √s_t]`` 上的精确最小值。二者与
    :func:`_normal_thickness_curvature_search` 共用 :func:`_monotone_slope_tolerance`。

    旧实现改用 ``samples // 2`` 点均匀网格上的差分来判单调性，会漏掉落在两个采样点
    之间的窄幅回升；更重要的是它的容差与搜索判据相差三个数量级，于是出现「搜索说
    可行、门禁说不可行」的自相矛盾。解析判据没有网格，也就没有这个问题。

    峰值位置仍然解析判定：构造已保证 ``h'(s_t) = 0``，只需再确认 ``h'(s) ≥ 0``
    （``s < s_t``）与 ``h'(s) ≤ 0``（``s > s_t``），峰值就唯一落在 ``s_t``——不依赖
    ``s_t`` 是否落在均匀网格上（实测 ``s_t = 0.33332`` 用固定网格容差会被误判）。
    """

    station = np.linspace(0.0, 1.0, samples)
    half = law(station)
    hmax = law.half_thickness_max
    tolerance = _monotone_slope_tolerance(hmax)
    if not np.all(np.isfinite(half)):
        raise ThicknessLawInfeasible("厚度律产生非有限值")
    # h ~ a1 sqrt(s)：最靠前的采样点本身正确趋于 0，排除后再检查正性。
    interior = slice(1, -1)
    if np.any(half[interior] < -1e-12 * hmax):
        worst = float(station[interior][int(np.argmin(half[interior]))])
        raise ThicknessLawInfeasible(
            f"厚度律在 s={worst:.6f} 处出现负值：h={float(np.min(half[interior])):.6e}"
        )
    highest = np.maximum.accumulate(half)
    rise = float(np.max(half - highest))
    if rise > 1e-9 * hmax:
        worst = float(station[int(np.argmax(half - highest))])
        raise ThicknessLawInfeasible(f"厚度律非单调：在 s={worst:.6f} 处回升 {rise:.3e}")
    if abs(float(law.derivative(np.array([law.max_thickness_station]))[0])) > tolerance:
        raise ThicknessLawInfeasible(
            f"最大厚度点 s={law.max_thickness_station:.6f} 处 h' 不为零："
            f"{float(law.derivative(np.array([law.max_thickness_station]))[0]):.3e}"
        )
    split = law.max_thickness_station
    length = law.aft_length
    # 门禁直接审计**已构造的**系数，而不是重新跑一遍搜索：这样即使搜索与构造之间出现
    # 任何不一致，也会在这里暴露，而不是被同义反复地掩盖。
    aft_slope = _polynomial_extreme(law.aft_coefficients, 0.0, 1.0, largest=True) / length
    if aft_slope > tolerance:
        raise ThicknessLawInfeasible(
            f"最大厚度点之后出现正斜率（峰值不在 s_t）：解析最大 dh/ds = {aft_slope:.3e}"
        )
    fore_slope = _fore_slope_min_from_coefficients(law.fore_coefficients, split)
    if fore_slope < -tolerance:
        raise ThicknessLawInfeasible(
            f"最大厚度点之前出现负斜率（峰值不在 s_t）：解析最小 dh/ds = {fore_slope:.3e}"
        )
    peak = int(np.argmax(half))
    if len(half) > 1 and float(half[peak]) < float(half[0]) - 1e-12:
        raise ThicknessLawInfeasible("厚度律前缘值高于内部峰值")
    # 峰值数值同样用解析条件核对，不依赖网格是否对齐 s_t。
    if abs(float(law(np.array([law.max_thickness_station]))[0]) - hmax) > 1e-12 * hmax:
        raise ThicknessLawInfeasible(
            f"h(s_t) 与 max_thickness_ratio 不一致："
            f"{float(law(np.array([law.max_thickness_station]))[0]):.9f} vs {hmax:.9f}"
        )
    # 网格上的峰值必然不低于端点，且不高于解析峰值（超界即过冲）。
    if float(half[peak]) > hmax + 1e-9 * hmax:
        raise ThicknessLawInfeasible(
            f"厚度律过冲：网格峰值 {float(half[peak]):.9f} > h_max {hmax:.9f}"
        )
    if abs(float(half[-1]) - law.half_thickness_te) > 1e-12:
        raise ThicknessLawInfeasible("尾缘半厚度与本构值不一致")
    # 实现楔角按两侧叶面切线的真实夹角反算（含曲率项）；这是与请求值独立的检查，
    # 能抓住弧长归一化、出口金属角与 hκ 三处换算错误。
    realized = half_wedge_from_slope_deg(
        law.aft_end_slope,
        law.mean_line_length,
        outlet_metal_angle_deg,
        law.half_thickness_te,
        terminal_curvature,
    )
    if abs(realized - law.trailing_edge_half_wedge_deg) > 1e-7:
        raise ThicknessLawInfeasible(
            f"实现的尾缘半楔角 {realized:.9f}° 与请求值 "
            f"{law.trailing_edge_half_wedge_deg:.9f}° 不一致；"
            f"请检查弧长归一化因子、出口金属角与尾缘曲率换算"
        )


# --------------------------------------------------------------------------------------
# profile assembly
# --------------------------------------------------------------------------------------


def cosine_stations(count: int) -> np.ndarray:
    """``[0, 1]`` 上的余弦聚集站位（前缘与尾缘加密）。"""

    beta = np.linspace(0.0, math.pi, count)
    return 0.5 * (1.0 - np.cos(beta))


def surface_curvature(
    mean_curvature: np.ndarray,
    half: np.ndarray,
    half_slope: np.ndarray,
    half_second: np.ndarray,
    side: int,
) -> np.ndarray:
    """法向偏置叶面的有符号曲率（中弧线为分片圆弧，故 ``κ' = 0``）。

    ``half_slope``、``half_second`` 必须是**对真实弧长** ``t`` 的导数 ``dh/dt``、
    ``d²h/dt²``。厚度律 :class:`NormalThicknessLaw` 的参数是归一化弧长 ``s = t / L``，
    因此调用点必须传 ``h'(s)/L``、``h''(s)/L²``——直接代 ``dh/ds`` 会把叶面曲率放大
    ``L`` 倍（前缘半径报告值偏大 ``L`` 倍），这在 ``L > 1`` 的弯中弧线上是系统性错误。
    ``mean_curvature`` 本身就是真实曲率 ``κ = dφ/dt``，不需要缩放。

    ``side = +1`` 为吸力面（``M + h n``），``-1`` 为压力面。
    """

    factor = 1.0 - side * half * mean_curvature
    numerator = (
        mean_curvature * factor**2
        + side * factor * half_second
        + 2.0 * half_slope**2 * mean_curvature
    )
    return numerator / np.power(factor**2 + half_slope**2, 1.5)


def has_self_intersection(points: np.ndarray) -> bool:
    """闭合折线自交检测（共享端点的相邻段不计）。"""

    count = points.shape[0]
    if count < 4:
        return False
    start = points
    end = np.roll(points, -1, axis=0)
    i, j = np.triu_indices(count, k=2)
    keep = ~((i == 0) & (j == count - 1))
    i, j = i[keep], j[keep]
    p, q, r, s = start[i], end[i], start[j], end[j]
    denom = (q[:, 0] - p[:, 0]) * (s[:, 1] - r[:, 1]) - (q[:, 1] - p[:, 1]) * (
        s[:, 0] - r[:, 0]
    )
    delta_x, delta_y = r[:, 0] - p[:, 0], r[:, 1] - p[:, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (delta_x * (s[:, 1] - r[:, 1]) - delta_y * (s[:, 0] - r[:, 0])) / denom
        u = (delta_x * (q[:, 1] - p[:, 1]) - delta_y * (q[:, 0] - p[:, 0])) / denom
    valid = np.isfinite(t) & np.isfinite(u)
    hit = valid & (t > 1e-12) & (t < 1.0 - 1e-12) & (u > 1e-12) & (u < 1.0 - 1e-12)
    return bool(np.any(hit))


def osculating_radius(
    law: NormalThicknessLaw,
    mean_curvature: float,
    station: float,
    side: int,
) -> float:
    """叶面在归一化站位 ``station`` 处的密切圆半径（真实长度单位）。

    曲率公式按真实弧长推导，而 ``law`` 的导数是对归一化弧长的，故在此统一换算成
    ``dh/dt = h'/L``、``d²h/dt² = h''/L²``。
    """

    s = np.array([station])
    length = law.mean_line_length
    curvature = surface_curvature(
        np.array([mean_curvature]),
        law(s),
        law.derivative(s) / length,
        law.second_derivative(s) / length**2,
        side,
    )[0]
    return 1.0 / abs(float(curvature))


def leading_edge_radius_limit(
    law: NormalThicknessLaw,
    mean_curvature: float,
    side: int,
) -> float:
    """前缘密切圆半径的极限值（真实长度单位，``s -> 0``）。

    ``h ~ a1 sqrt(s)`` 给出 ``R(s) = R_0 (1 + O(sqrt(s)))``；用 ``s`` 与 ``s/100``
    两个探针点做 Richardson 外推即可消去 ``sqrt(s)`` 项。极限值是
    ``R_0 = a1^2 / (2 L)``（``L`` 为中弧线总弧长），**不是** ``a1^2 / 2``——
    :func:`osculating_radius` 负责把归一化弧长导数换算到真实弧长，本函数只做外推。
    """

    far = osculating_radius(law, mean_curvature, 1e-8, side)
    near = osculating_radius(law, mean_curvature, 1e-10, side)
    return near - (far - near) / 9.0


def generate_arc_profile(spec: ArcProfileSpec) -> ArcProfileGeometry:
    """生成完整圆弧族叶型（弦长归一化局部坐标）。"""

    chain = build_arc_chain(spec.mean_line)
    # 厚度律的参数是归一化弧长；尾缘楔角由两侧叶面切线的真实夹角定义，因此必须把
    # 中弧线总弧长、出口金属角与尾缘曲率一并传入做精确换算。
    law = build_thickness_law(
        spec,
        arc_length=chain.total_length,
        outlet_metal_angle_deg=spec.mean_line.outlet_metal_angle_deg,
        terminal_curvature=chain.arcs[-1].curvature,
    )

    stations = cosine_stations(spec.points_per_side)
    frame = chain.evaluate(stations)
    mean_points = frame["points"]
    tangents = frame["tangents"]
    normals = frame["normals"]
    mean_curvature = frame["curvature"]

    half = law(stations)
    half_slope = law.derivative(stations)
    half_second = law.second_derivative(stations)
    if not np.all(np.isfinite(half)):
        raise ArithmeticError("厚度律产生非有限值")
    if np.any(half[1:] <= 0.0):
        raise ArithmeticError("厚度律在叶型内部出现非正值")

    suction = mean_points + half[:, None] * normals
    pressure = mean_points - half[:, None] * normals

    clearance = 1.0 / np.abs(mean_curvature) - half
    if float(np.min(clearance[1:])) <= 0.0:
        raise ArithmeticError(
            "厚度超过中弧线曲率半径，压力面法向偏置自交: "
            f"min(r_c - h) = {float(np.min(clearance[1:])):.6e}"
        )
    if has_self_intersection(np.vstack([suction, pressure[::-1]])):
        raise ArithmeticError("叶型轮廓自交")

    # 前缘处 h' 与 h'' 发散（sqrt 前缘律），曲率只在 s > 0 的站位上评估。
    # 曲率公式按**真实弧长**推导，而厚度律的导数是对归一化弧长的：必须除以 L 与 L²。
    arclength = law.mean_line_length
    suction_curvature = surface_curvature(
        mean_curvature[1:], half[1:], half_slope[1:] / arclength, half_second[1:] / arclength**2, 1
    )
    pressure_curvature = surface_curvature(
        mean_curvature[1:], half[1:], half_slope[1:] / arclength, half_second[1:] / arclength**2, -1
    )

    probe_station = np.array([1e-9])
    le_radius_probe = 1.0 / abs(
        float(
            surface_curvature(
                np.array([mean_curvature[0]]),
                law(probe_station),
                law.derivative(probe_station) / arclength,
                law.second_derivative(probe_station) / arclength**2,
                1,
            )[0]
        )
    )
    le_radius_limit = leading_edge_radius_limit(law, float(mean_curvature[0]), 1)
    wedge_suction, wedge_pressure = trailing_edge_half_wedges_deg(
        law, spec.mean_line.outlet_metal_angle_deg, chain.arcs[-1].curvature
    )
    requested_le_radius = spec.leading_edge_radius_ratio
    # 密切圆必须用**真实**前缘曲率半径 a1^2/(2L)，而不是厚度律的 a1^2/2 标称值：
    # 归一化弧长参数让真实半径缩短 L 倍，用标称值画出的圆会偏离叶面。
    leading_edge_radius = 0.5 * law.fore_coefficients[0] ** 2 / arclength
    leading_edge_center = mean_points[0] + leading_edge_radius * tangents[0]
    leading_edge_angles = np.linspace(0.0, 2.0 * math.pi, 161)
    leading_edge_circle = leading_edge_center + leading_edge_radius * np.column_stack(
        [np.cos(leading_edge_angles), np.sin(leading_edge_angles)]
    )

    # 精确峰值：厚度在 s_t 处达到最大值，不依赖离散采样。
    peak_frame = chain.evaluate(np.array([spec.max_thickness_station]))
    peak_thickness = float(2.0 * law(np.array([spec.max_thickness_station]))[0])
    apex_x, apex_y = chain.apex()
    chain_invariants = check_chain_invariants(chain)

    # 由叶面切向量 dS/ds、dP/ds（含 L 与 hκ）算出的绝对切线角；半楔角是二者之差的
    # 一半。注意这是**解析**审计量：与「请求值」相互独立（不共用反解结果），
    # 而完全独立的折线反算在 tests/test_circular_arc.py 里做。
    measured = 0.5 * abs(wedge_suction - wedge_pressure)
    features = {
        "arc_count": float(chain.arc_count),
        "inlet_metal_angle_deg": float(math.degrees(chain.node_angles[0])),
        "outlet_metal_angle_deg": float(-math.degrees(chain.node_angles[-1])),
        "camber_angle_deg": float(math.degrees(chain.node_angles[0] - chain.node_angles[-1])),
        "camber_fraction_le": float(
            math.degrees(chain.node_angles[0])
            / math.degrees(chain.node_angles[0] - chain.node_angles[-1])
        ),
        "max_thickness_ratio": peak_thickness,
        "max_thickness_station": float(spec.max_thickness_station),
        "max_thickness_x_ratio": float(peak_frame["points"][0, 0]),
        "max_camber_ratio": float(apex_y),
        "max_camber_x_ratio": float(apex_x),
        "leading_edge_radius_ratio": float(requested_le_radius),
        "trailing_edge_thickness_ratio": float(2.0 * law.half_thickness_te),
        "trailing_edge_half_wedge_deg": float(measured),
        "chord_closure_error_ratio": chain_invariants["chord_closure_error"],
        "mean_line_arc_length_ratio": float(chain.total_length),
    }
    diagnostics = {
        **chain_invariants,
        # 三个前缘半径量都以**真实弧长**为单位：a1^2/(2L)。若把归一化弧长导数直接
        # 代入曲率公式，三者都会偏大 L 倍（默认 DCA30/20 下约 3.1%）。
        "leading_edge_radius_probe_ratio": float(le_radius_probe),
        "leading_edge_radius_limit_ratio": float(le_radius_limit),
        "leading_edge_radius_analytic_ratio": float(leading_edge_radius),
        "leading_edge_radius_requested_ratio": float(requested_le_radius),
        "mean_line_arclength_factor": float(arclength),
        "trailing_edge_surface_angle_suction_deg": wedge_suction,
        "trailing_edge_surface_angle_pressure_deg": wedge_pressure,
        "trailing_edge_half_wedge_requested_deg": float(spec.trailing_edge_half_wedge_deg),
        "aft_end_slope": float(law.aft_end_slope),
        "pressure_offset_clearance_min": float(np.min(clearance[1:])),
        "suction_max_abs_curvature": float(np.max(np.abs(suction_curvature))),
        "pressure_max_abs_curvature": float(np.max(np.abs(pressure_curvature))),
        "suction_pressure_gap_min": float(
            np.min(np.linalg.norm(suction[1:] - pressure[1:], axis=1))
        ),
        "self_intersection": 0.0,
    }

    return ArcProfileGeometry(
        profile_id=spec.profile_id,
        mean_line_id=spec.mean_line.mean_line_id,
        designation=build_designation(spec),
        spec=spec,
        suction=suction.tolist(),
        pressure=pressure.tolist(),
        camber=mean_points.tolist(),
        thickness=(2.0 * half).tolist(),
        normals=normals.tolist(),
        arc_parameter=stations.tolist(),
        node_points=chain.node_points.tolist(),
        node_angles_deg=np.degrees(chain.node_angles).tolist(),
        node_stations=(chain.node_points[:, 0] / chain.chord).tolist(),
        arc_radii=[float(arc.radius) for arc in chain.arcs],
        arc_turns_deg=[float(math.degrees(arc.turn)) for arc in chain.arcs],
        arc_centers=[arc.center.tolist() for arc in chain.arcs],
        leading_edge_circle=leading_edge_circle.tolist(),
        trailing_edge_segment=[suction[-1].tolist(), pressure[-1].tolist()],
        features=features,
        diagnostics=diagnostics,
        source_provenance=dict(SOURCE_PROVENANCE),
        metadata={
            "definition": (
                "N-arc internally tangent circular-arc mean line; spans solved from two "
                "linear closure conditions sum(delta)=1 and sum(delta*tan(mu))=0"
            ),
            "thickness_application": (
                "symmetric, normal to mean line; station parameter is normalized mean-line "
                "arc length"
            ),
            "leading_edge_contract": (
                "natural rounded nose from h ~ a1*sqrt(s) with s the normalized mean-line "
                "arc length; osculating radius a1^2/(2L) (L = total mean-line arc length, "
                ">= 1), independent of mean-line curvature; no cap blending"
            ),
            "trailing_edge_contract": "flat truncation normal to the mean line",
            "family": str(spec.mean_line.family),
            "closure": str(spec.mean_line.closure),
            "algorithm_version": ARC_ALGORITHM_VERSION,
            "coordinate_convention": ARC_COORDINATE_CONVENTION,
        },
    )


def arc_feasibility_report(spec: ArcProfileSpec) -> dict[str, Any]:
    """圆弧族叶型的可行域报告（Web ``/api/v2/arc/limits`` 与 CLI ``arc limits`` 共用）。

    报告的三类边界互不相同，不能互相替代：

    * ``maximum_feasible_half_wedge_deg``——厚度合同能实现的最大尾缘半楔角。它依赖中弧线
      几何（总弧长 ``L``、出口金属角 ``χ₂``、尾缘曲率 ``κ``），因此必须用**同一条**中弧线
      求值，否则会报出一个生成器实现不了的数（旧实现的真实缺陷）。
    * ``junction_station_range`` / ``junction_angle_bounds_deg``——双圆弧切点的可达站位与
      切线角区间；单圆弧没有切点自由度，多圆弧不用 ``junction_station``。
    * ``interior_node_station_bounds``——多圆弧每个内节点站位的可达区间（其余站位固定）。
      空区间用 ``None`` 表示，与「区间很窄」严格区分。

    ``family`` 决定了哪一组字段有意义：不适用的字段返回 ``None`` 而不是伪造一个数值。
    """

    mean = spec.mean_line
    chain = build_arc_chain(mean)
    law_ceiling = maximum_feasible_half_wedge_deg(
        spec.max_thickness_ratio,
        spec.max_thickness_station,
        spec.trailing_edge_thickness_ratio,
        arc_length=chain.total_length,
        outlet_metal_angle_deg=mean.outlet_metal_angle_deg,
        terminal_curvature=chain.arcs[-1].curvature,
        leading_edge_radius_ratio=spec.leading_edge_radius_ratio,
    )
    # 厚度律的数学上界可以高于参数域的硬上限（``t/c=0.25``、``s_t=0.75`` 时约 54°）。
    # 对外报告的必须是**有效**上界，否则界面会把滑动条上限设成模型不接受的值。
    # 参数域是**开**区间 ``(0.5, 45)``，所以上限截断到 ``45.0`` 以下最近的可表示值：
    # 报出 45.0 会让「取到上界」本身变成校验错误。**下限同样要处理**：数学上界可能落在
    # 0.5 以下（实测低至 2.4e-9，甚至恰为 0.0），那时该合同连最小允许楔角都实现不了，
    # 报一个 0.2344 之类的数只会让用户拿到一个必然 422 的输入。这种情况报 ``None``。
    ceiling: float | None
    if law_ceiling > TRAILING_EDGE_HALF_WEDGE_MIN_DEG:
        ceiling = min(law_ceiling, math.nextafter(TRAILING_EDGE_HALF_WEDGE_MAX_DEG, 0.0))
    else:
        ceiling = None

    # 上界只回答「楔角可行不可行」，不回答「这条 spec 能不能生成」。厚度超过中弧线曲率
    # 半径时压力面法向偏置自交，生成会在任何楔角下失败，而那个判据与楔角无关。因此这里
    # 真的试生成一次，把几何可行性单独报出来，避免 ``wedge_feasible`` 被读成「能生成」。
    geometry_feasible = True
    geometry_error: str | None = None
    try:
        generate_arc_profile(spec)
    except (ArithmeticError, ValueError) as exc:
        geometry_feasible = False
        geometry_error = str(exc)

    junction_stations: list[float] | None = None
    junction_angles: list[float] | None = None
    if mean.family == ArcFamily.DOUBLE:
        low, high = junction_station_range(
            mean.inlet_metal_angle_deg, mean.outlet_metal_angle_deg
        )
        junction_stations = [low, high]
        angle_low, angle_high = junction_station_bounds_deg(
            mean.inlet_metal_angle_deg, mean.outlet_metal_angle_deg
        )
        junction_angles = [angle_low, angle_high]

    station_bounds: list[list[float] | None] | None = None
    if mean.family == ArcFamily.MULTIPLE:
        station_bounds = [
            None if math.isnan(low) else [low, high]
            for low, high in interior_station_bounds(
                mean.inlet_metal_angle_deg,
                mean.outlet_metal_angle_deg,
                mean.interior_node_angles_deg,
                mean.interior_node_stations,
            )
        ]

    return {
        "family": str(mean.family),
        "arc_count": mean.arc_count,
        "closure": str(mean.closure),
        "maximum_feasible_half_wedge_deg": ceiling,
        # 报出的上界是否存在：该合同连最小允许楔角都实现不了时为 False
        "wedge_bound_exists": ceiling is not None,
        "thickness_law_half_wedge_ceiling_deg": law_ceiling,
        "half_wedge_domain_deg": [
            TRAILING_EDGE_HALF_WEDGE_MIN_DEG,
            TRAILING_EDGE_HALF_WEDGE_MAX_DEG,
        ],
        "requested_half_wedge_deg": spec.trailing_edge_half_wedge_deg,
        "wedge_feasible": ceiling is not None
        and spec.trailing_edge_half_wedge_deg <= ceiling,
        # 与楔角无关的几何可行性（法向偏置自交、厚度超过中弧线曲率半径等）
        "geometry_feasible": geometry_feasible,
        "geometry_error": geometry_error,
        "mean_line_arc_length": chain.total_length,
        "terminal_curvature": chain.arcs[-1].curvature,
        "junction_station_range": junction_stations,
        "junction_angle_bounds_deg": junction_angles,
        "interior_node_stations": list(mean.interior_node_stations),
        "interior_node_station_bounds": station_bounds,
        "leading_edge_radius_requested_ratio": spec.leading_edge_radius_ratio,
        # 真实前缘极限半径 = a₁²/(2L) = r_LE_标称 / L。界面上必须同时给出这两个数，
        # 否则「输入 0.006 却看到 0.005819」会被当成缺陷。
        "leading_edge_radius_limit_ratio": spec.leading_edge_radius_ratio
        / chain.total_length,
    }


def default_double_arc_spec(**overrides) -> ArcProfileSpec:
    """默认双圆弧叶型：``chi1 = 30°``、``chi2 = 20°``、最大弯度处相切。"""

    mean = overrides.pop("mean_line", None)
    if mean is None:
        mean = ArcMeanLineSpec(
            family=ArcFamily.DOUBLE,
            inlet_metal_angle_deg=30.0,
            outlet_metal_angle_deg=20.0,
            closure=MeanLineClosure.MAX_CAMBER,
        )
    return ArcProfileSpec(mean_line=mean, **overrides)
