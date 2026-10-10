"""圆弧族叶型（单圆弧 / 双圆弧 / 多圆弧中弧线）的模型合同。

定义依据与完整推导见 ``docs/circular-arc-profiles.md``。核心合同：

* 中弧线是 ``N`` 段**内切**圆弧链，相邻圆弧在公共节点处共享切线（G1 连续，
  曲率分片常值，节点处允许曲率跳变）。
* 节点切线角 ``phi`` 以弦线为基准：``phi_0 = +chi1`` 于前缘，``phi_N = -chi2`` 于尾缘。
* 每段圆弧的**弦向跨度占比** ``delta`` 满足两条线性闭合条件
  ``sum(delta) = 1`` 与 ``sum(delta * tan(mu)) = 0``（``mu`` 为该段平均切线角）。
  因此整条圆弧链闭式可解，无迭代、无最小二乘。
"""

from __future__ import annotations

import hashlib
import json
import math
from enum import StrEnum
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import _round_floats

ARC_SCHEMA_VERSION = "1.0.0"
ARC_ALGORITHM_VERSION = "circular-arc-chain-v1"
ARC_COORDINATE_CONVENTION = "chord-local:x-le-to-te,y-to-suction"
TANGENT_ANGLE_LIMIT_DEG = 89.0
# 单圆弧的两端切线角必须相等——这是闭合条件 ``phi_0 + phi_1 = 0`` 的唯一解，是几何
# 事实而不是约定。但浮点表示无法要求逐位相等，因此给出一个**模型与生成器共用**的容差。
#
# 容差不能任意放宽：两端角相差 ``Δ`` 时弦线闭合误差是 ``|tan(Δ/2)|``（弧度），而
# ``check_chain_invariants`` 的弦线门限是 ``1e-12·max(1, r_max)``。``1e-11`` 度对应
# ``8.7e-14`` 的闭合误差，留出两个数量级余量；旧版模型用 ``1e-9`` 度，生成器却只接受到
# ``1.146e-10`` 度，中间那条缝里模型说合法、生成器说非法。
SINGLE_ARC_ANGLE_TOLERANCE_DEG = 1e-11
# 尾缘半楔角的**硬**允许域。厚度律的数学可行上界可以更高（``t/c=0.25``、``s_t=0.75``
# 时约 54°），但超过这里的值属于本合同的参数域之外，因此对外报告的「可行上界」必须
# 与它取交——否则界面会把滑动条上限设成一个模型根本不接受的值。
TRAILING_EDGE_HALF_WEDGE_MIN_DEG = 0.5
TRAILING_EDGE_HALF_WEDGE_MAX_DEG = 45.0


class ArcFamily(StrEnum):
    """圆弧链段数：1（单圆弧）、2（双圆弧）、>=3（多圆弧）。"""

    SINGLE = "single"
    DOUBLE = "double"
    MULTIPLE = "multiple"


class MeanLineClosure(StrEnum):
    """双圆弧中弧线第三个自由度（切点）的闭合方式。"""

    MAX_CAMBER = "max_camber"
    JUNCTION_STATION = "junction_station"
    NODE_ANGLES = "node_angles"


class ArcMeanLineSpec(BaseModel):
    """``N`` 段内切圆弧中弧线的完整定义（自由度 ``2N - 1``）。

    输入为「``N+1`` 个节点切线角 + ``N-2`` 个内节点弦向站位」，其余量由线性闭合条件解出。
    """

    model_config = ConfigDict(frozen=True)

    family: ArcFamily = ArcFamily.DOUBLE
    inlet_metal_angle_deg: float = Field(default=30.0, gt=0.0, lt=TANGENT_ANGLE_LIMIT_DEG)
    outlet_metal_angle_deg: float = Field(default=20.0, gt=0.0, lt=TANGENT_ANGLE_LIMIT_DEG)
    closure: MeanLineClosure = MeanLineClosure.MAX_CAMBER
    junction_station: float | None = Field(default=None, gt=0.0, lt=1.0)
    interior_node_angles_deg: tuple[float, ...] = ()
    interior_node_stations: tuple[float, ...] = ()

    @property
    def arc_count(self) -> int:
        if self.family == ArcFamily.SINGLE:
            return 1
        if self.family == ArcFamily.DOUBLE:
            return 2
        return len(self.interior_node_angles_deg) + 1

    @property
    def node_count(self) -> int:
        return self.arc_count + 1

    @property
    def camber_angle_deg(self) -> float:
        return self.inlet_metal_angle_deg + self.outlet_metal_angle_deg

    @property
    def camber_fraction_le(self) -> float:
        return self.inlet_metal_angle_deg / self.camber_angle_deg

    def resolved_node_angles_deg(self) -> tuple[float, ...]:
        """全部 ``N+1`` 个节点切线角（度），含由闭合条件确定的内节点角。"""

        if self.family != ArcFamily.DOUBLE:
            return (
                self.inlet_metal_angle_deg,
                *self.interior_node_angles_deg,
                -self.outlet_metal_angle_deg,
            )
        if self.closure == MeanLineClosure.MAX_CAMBER:
            middle = 0.0
        elif self.closure == MeanLineClosure.NODE_ANGLES:
            middle = self.interior_node_angles_deg[0]
        else:
            from .circular_arc import solve_junction_angle_for_station

            assert self.junction_station is not None
            middle = solve_junction_angle_for_station(
                self.inlet_metal_angle_deg,
                self.outlet_metal_angle_deg,
                self.junction_station,
            )
        return (self.inlet_metal_angle_deg, middle, -self.outlet_metal_angle_deg)

    @model_validator(mode="before")
    @classmethod
    def resolve_multi_arc_stations(cls, data: Any) -> Any:
        """多圆弧缺少内节点弦向站位时，在**验证之前**自动求解并写回。

        站位是**派生量**而不是自由参数：给定全部节点切线角后，两条线性闭合条件只留下
        ``N-2`` 维仿射可行域，:func:`build_mca_mean_line` 在可行域非空时能解出一组严格
        为正的跨度。因此缺省自动求解，让 API / CLI / Web 三条路径共用同一行为——旧版
        只有 CLI 做了这层回退，于是网页端的多圆弧 100% 报「需要 N-2 个内节点弦向站位」。

        必须用 ``mode="before"``：``mode="after"`` 的校验器返回新实例会被 pydantic 在
        ``__init__`` 路径上丢弃（并发出 ``UserWarning``），调用方拿到的仍是未填充的对象，
        ``mean_line_id`` 也就会基于错误的站位计算。
        """

        if not isinstance(data, dict):
            return data
        family = data.get("family", ArcFamily.DOUBLE)
        if family != ArcFamily.MULTIPLE and str(family) != ArcFamily.MULTIPLE.value:
            return data
        angles = tuple(data.get("interior_node_angles_deg") or ())
        if data.get("interior_node_stations") or len(angles) < 2:
            return data
        inlet = float(data.get("inlet_metal_angle_deg", 30.0))
        outlet = float(data.get("outlet_metal_angle_deg", 20.0))
        resolved = build_mca_mean_line(
            inlet, outlet, (inlet, *(float(angle) for angle in angles), -outlet)
        )
        return {**data, "interior_node_stations": resolved.interior_node_stations}

    @model_validator(mode="after")
    def check_contract(self) -> ArcMeanLineSpec:
        family = self.family
        interior_angles = self.interior_node_angles_deg
        interior_stations = self.interior_node_stations
        if family == ArcFamily.SINGLE:
            if interior_angles or interior_stations:
                raise ValueError("单圆弧中弧线不允许内节点参数")
            # 判据必须与生成器**逐位相同**：生成器在弧度域比较
            # ``|degrees(radians(χ1) − radians(χ2))|``。直接比较原始度数值看似等价，
            # 实则更严——``radians(30.0) == radians(29.999999999999996)`` 为真，
            # 那种输入生成器接受而模型拒绝，于是模型说非法、生成器说合法。
            difference = abs(
                math.degrees(
                    math.radians(self.inlet_metal_angle_deg)
                    - math.radians(self.outlet_metal_angle_deg)
                )
            )
            if difference > SINGLE_ARC_ANGLE_TOLERANCE_DEG:
                raise ValueError("单圆弧的两端切线角必须相等（闭合条件的唯一解）")
            if self.closure != MeanLineClosure.MAX_CAMBER:
                raise ValueError("单圆弧没有切点自由度，闭合方式固定为 max_camber")
        elif family == ArcFamily.DOUBLE:
            if interior_stations:
                raise ValueError("双圆弧只有一个内节点，不接受内节点站位")
            if self.closure == MeanLineClosure.NODE_ANGLES:
                if len(interior_angles) != 1:
                    raise ValueError("双圆弧以节点角闭合时必须给出恰好一个内节点切线角")
            elif interior_angles:
                raise ValueError("双圆弧的切点切线角由 closure 决定，不能同时显式给出")
            if self.closure == MeanLineClosure.JUNCTION_STATION:
                if self.junction_station is None:
                    raise ValueError("junction_station 闭合必须给出切点弦向站位")
            elif self.junction_station is not None:
                raise ValueError("junction_station 仅在 junction_station 闭合下有效")
        else:
            if len(interior_angles) < 2:
                raise ValueError("多圆弧至少三段，需要至少两个内节点切线角")
            if len(interior_stations) != self.arc_count - 2:
                raise ValueError(
                    f"多圆弧需要 N-2 = {self.arc_count - 2} 个内节点弦向站位，"
                    f"收到 {len(interior_stations)} 个"
                )
            if self.junction_station is not None:
                raise ValueError("多圆弧不使用 junction_station")

        # 全部节点切线角必须严格递减。这是圆弧链可解的**必要**条件，不是充分条件：
        # 严格递减仍可能给出非正的弦向跨度（例如 χ₁=30°、χ₂=1°、切点角 20° 时
        # δ = [−0.5597, 1.5597]），那一步由 build_arc_chain 的 δ > 0 门禁拒绝。
        # 双圆弧与多圆弧的跨度都有闭式/线性解，因此在这里就提前给出可读的失败原因，
        # 让「模型说合法」真的蕴含「生成器能构造」。
        angles = self.resolved_node_angles_deg()
        if any(abs(angle) >= TANGENT_ANGLE_LIMIT_DEG for angle in angles):
            raise ValueError("节点切线角必须位于 (-90, 90) 度之内")
        # 递减必须在**弧度域**成立：两个不同的度数可能塌缩成同一个弧度，此时圆弧转角
        # 为零、半径公式除零。生成器用弧度建几何，因此这里也按弧度判。
        radians = [math.radians(angle) for angle in angles]
        for previous, current in zip(radians, radians[1:], strict=False):
            if not current < previous:
                raise ValueError(
                    f"节点切线角必须严格递减（按弧度判定），收到 "
                    f"{[round(value, 6) for value in angles]}"
                )
        if family == ArcFamily.DOUBLE:
            from .circular_arc import double_arc_first_span

            span = double_arc_first_span(radians[0], radians[2], radians[1])
            if not 0.0 < span < 1.0:
                raise ValueError(
                    f"该切点切线角使前段弦向跨度为 {span:.6f}，不在 (0, 1) 内："
                    f"圆弧链无法闭合。请改用可达区间内的切点角或 junction_station 闭合。"
                )
            # 末两段跨度的分母是 ``tan μ_{N-1} − tan μ_N``；金属角极小时它会下溢到
            # ``1e-15`` 以下，生成器随即报「平均切线角退化」。在这里提前拦下。
            tan_first = math.tan(0.5 * (radians[0] + radians[1]))
            tan_last = math.tan(0.5 * (radians[1] + radians[2]))
            if abs(tan_first - tan_last) < 1e-15:
                raise ValueError(
                    "双圆弧的末两段平均切线角过于接近，跨度方程退化："
                    "请增大进出口金属角之差。"
                )
        if family == ArcFamily.MULTIPLE:
            from .circular_arc import solve_span_distribution

            try:
                solve_span_distribution(np.asarray(radians, dtype=float), interior_stations)
            except ValueError as exc:
                raise ValueError(
                    f"该节点角/站位组合无法闭合（弦向跨度出现非正值）：{exc}"
                ) from exc
        # 每段圆弧凹向必须一致（圆心同侧）：等价于该段的平均切线角不跨越 0 的两侧
        # 之外的符号翻转。要求圆弧整体位于弦线同侧时由 build_arc_chain 的
        # delta > 0 断言兜底；这里只拒绝切线角跨度达到或超过 180 度的段落。
        for previous, current in zip(angles, angles[1:], strict=False):
            if previous - current >= 180.0:
                raise ValueError("单段圆弧的转角必须小于 180 度")

        if any(not 0.0 < station < 1.0 for station in interior_stations):
            raise ValueError("内节点弦向站位必须位于 (0, 1)")
        for previous, current in zip(interior_stations, interior_stations[1:], strict=False):
            if not current > previous:
                raise ValueError("内节点弦向站位必须严格递增")
        return self

    @property
    def mean_line_id(self) -> str:
        payload = {
            "schema": ARC_SCHEMA_VERSION,
            "algorithm": ARC_ALGORITHM_VERSION,
            "coordinate_convention": ARC_COORDINATE_CONVENTION,
            "family": str(self.family),
            "closure": str(self.closure),
            "node_angles_deg": [round(value, 12) for value in self.resolved_node_angles_deg()],
            "interior_node_stations": [round(value, 12) for value in self.interior_node_stations],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "arcml_" + hashlib.sha256(raw).hexdigest()[:24]


class ArcProfileSpec(BaseModel):
    """圆弧族叶型的完整、可重建生成参数（弦长归一化，无量纲）。

    尾缘半楔角按**弦线局部坐标**中两侧叶面切线的夹角之半定义。叶面
    ``S = M + h n``、``P = M − h n`` 的切向量含弧长因子 ``L`` 与曲率项 ``hκ``，因此
    ``β`` 与厚度律末端斜率之间**不是**简单的正切比例，由
    :func:`circular_arc.aft_end_slope_for_wedge` 精确反解。

    只有**直线中弧线**（``L = 1``、``χ₂ = 0``、``κ = 0``）才退化为教科书恒等式
    ``h'(1) = −tan β``；默认 DCA30/20 请求 6° 时实际是 ``h'(1) = −0.108379``，而不是
    退化值 ``−0.105104``（见 ``docs/circular-arc-profiles.md`` §2.3）。
    """

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1.0.0"] = ARC_SCHEMA_VERSION
    algorithm_version: Literal["circular-arc-chain-v1"] = ARC_ALGORITHM_VERSION
    coordinate_convention: Literal["chord-local:x-le-to-te,y-to-suction"] = (
        ARC_COORDINATE_CONVENTION
    )
    mean_line: ArcMeanLineSpec = Field(default_factory=ArcMeanLineSpec)
    thickness_law: Literal["normal-sqrt-quartic-quintic-v1"] = "normal-sqrt-quartic-quintic-v1"
    edge_contract: Literal["natural-round-le-truncated-te"] = "natural-round-le-truncated-te"
    max_thickness_ratio: float = Field(default=0.10, ge=0.02, le=0.25)
    max_thickness_station: float = Field(
        default=0.40,
        ge=0.20,
        le=0.75,
        description="最大法向厚度所在位置，按中弧线归一化弧长计。",
    )
    leading_edge_radius_ratio: float = Field(default=0.006, ge=0.0005, le=0.05)
    trailing_edge_thickness_ratio: float = Field(default=0.006, ge=0.0005, le=0.05)
    trailing_edge_half_wedge_deg: float = Field(
        default=6.0,
        gt=TRAILING_EDGE_HALF_WEDGE_MIN_DEG,
        lt=TRAILING_EDGE_HALF_WEDGE_MAX_DEG,
    )
    points_per_side: int = Field(default=201, ge=51, le=1001)

    @model_validator(mode="after")
    def check_thickness_contract(self) -> ArcProfileSpec:
        if self.trailing_edge_thickness_ratio >= self.max_thickness_ratio:
            raise ValueError("尾缘厚度必须小于最大厚度")
        if self.leading_edge_radius_ratio > 0.5 * self.max_thickness_ratio:
            raise ValueError("前缘半径不得超过最大半厚度")
        return self

    @property
    def profile_id(self) -> str:
        payload = _round_floats(self.model_dump(mode="json"))
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "arcgeo_" + hashlib.sha256(raw).hexdigest()[:24]


class ArcProfileGeometry(BaseModel):
    """生成的圆弧族叶型几何（弦长归一化局部坐标）。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    schema_version: Literal["1.0.0"] = ARC_SCHEMA_VERSION
    profile_id: str
    mean_line_id: str
    designation: str
    spec: ArcProfileSpec
    suction: list[list[float]]
    pressure: list[list[float]]
    camber: list[list[float]]
    thickness: list[float]
    normals: list[list[float]]
    arc_parameter: list[float]
    node_points: list[list[float]]
    node_angles_deg: list[float]
    node_stations: list[float]
    arc_radii: list[float]
    arc_turns_deg: list[float]
    arc_centers: list[list[float]]
    leading_edge_circle: list[list[float]]
    trailing_edge_segment: list[list[float]]
    features: dict[str, float]
    diagnostics: dict[str, float]
    source_provenance: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def arrays(self):
        import numpy as np

        return tuple(
            np.asarray(value)
            for value in (self.suction, self.pressure, self.camber, self.thickness)
        )


def _weight_span_initial(radians: np.ndarray) -> np.ndarray:
    """按 ``1/(2 sin(Δ_i/2) cos μ_i)`` 归一化给出跨度初值，并解出末两段。

    该权重**既不是等弧长解也不是等半径解**：隐含半径是 ``r_i = δ_i/(2 sin(Δ_i/2) cos μ_i)``，
    即权重的二次函数 ``δ_i² / Σδ``；两条闭合条件又只由末两段承担，所以这只是一种
    启发式初值，一般节点角序列下不保证落在可行域内。
    """

    mu = 0.5 * (radians[:-1] + radians[1:])
    weight = 1.0 / (2.0 * np.sin(0.5 * (radians[:-1] - radians[1:])) * np.cos(mu))
    delta = weight / weight.sum()
    tan_mu = np.tan(mu)
    if delta.size > 2:
        fixed = delta[:-2]
        remainder = 1.0 - float(fixed.sum())
        moment = -float((fixed * tan_mu[:-2]).sum())
        a, b = float(tan_mu[-2]), float(tan_mu[-1])
        delta = np.concatenate([fixed, [((moment - b * remainder) / (a - b)), 0.0]])
        delta[-1] = remainder - delta[-2]
    return delta


def _analytic_positive_span_distribution(tan_mu: np.ndarray) -> np.ndarray | None:
    """仿射可行域 ``{Σδ = 1, δ·tanμ = 0, δ > 0}`` 的一个**显式内点**（存在时）。

    存在性有精确判据：``δ > 0`` 且 ``δ·tanμ = 0`` 要求 0 是 ``tanμ`` 的严格凸组合，
    因此可行域非空 **当且仅当** ``min tanμ < 0 < max tanμ``。满足时按下式构造：

    取 ``i = argmin tanμ``、``j = argmax tanμ``，其余分量置 ``ε``，再解两条等式

    .. math::
        \\delta_i + \\delta_j = 1 - (n-2)\\varepsilon,\\qquad
        t_i\\delta_i + t_j\\delta_j = -\\varepsilon\\sum_{k \\ne i,j} t_k .

    由 ``t_i < 0 < t_j`` 可知 ``\\varepsilon = 0`` 时 ``\\delta_i = -t_j/(t_i-t_j) > 0``、
    ``\\delta_j = t_i/(t_i-t_j) > 0``；两个分量关于 ``ε`` 连续，故足够小的 ``ε`` 仍然
    严格为正。``ε`` 从 ``1/(n·10³)`` 起对半收缩直到两个分量都为正（最多 60 次）。

    这个函数存在的理由：HiGHS 的原始可行性容差是 ``1e-7``，当所需跨度只有
    ``1e-8…1e-14``（相邻节点角几乎相等）时线性规划会返回 ``t = 0``，于是
    :func:`_maximin_span_distribution` 误报「可行域为空」——而判据本身是错的。
    """

    if tan_mu.size < 2:
        return None
    index_low = int(np.argmin(tan_mu))
    index_high = int(np.argmax(tan_mu))
    if not (float(tan_mu[index_low]) < 0.0 < float(tan_mu[index_high])):
        return None
    others = [index for index in range(tan_mu.size) if index not in (index_low, index_high)]
    low = float(tan_mu[index_low])
    high = float(tan_mu[index_high])
    others_moment = float(tan_mu[others].sum()) if others else 0.0
    epsilon = 1.0 / (tan_mu.size * 1000.0)
    for _ in range(60):
        spans = np.full(tan_mu.size, epsilon)
        total = 1.0 - len(others) * epsilon
        moment = -epsilon * others_moment
        spans[index_low] = (moment - high * total) / (low - high)
        spans[index_high] = total - spans[index_low]
        if np.all(spans > 0.0):
            return spans
        epsilon *= 0.5
    return None


def _maximin_span_distribution(tan_mu: np.ndarray) -> np.ndarray | None:
    """仿射可行域 ``{Σδ = 1, δ·tanμ = 0, δ > 0}`` 上的「最大最小跨度」解。

    这是一个标准线性规划：最大化 ``min_i δ_i``。可行域非空时它一定给出严格为正的
    跨度序列（这正是需要的能力——权重初值可能给出负跨度，但可行域其实是非空的）；
    可行域为空时返回 ``None``。

    线性规划失败时退回到 :func:`_analytic_positive_span_distribution` 的显式内点：
    判据是精确的，而 HiGHS 的原始可行性容差是 ``1e-7``，所需跨度更小时会误报不可行。
    """

    from scipy.optimize import linprog

    count = tan_mu.size
    objective = np.zeros(count + 1)
    objective[-1] = -1.0
    equality = np.zeros((2, count + 1))
    equality[0, :count] = 1.0
    equality[1, :count] = tan_mu
    inequality = np.zeros((count, count + 1))
    inequality[np.arange(count), np.arange(count)] = -1.0
    inequality[:, -1] = 1.0
    result = linprog(
        objective,
        A_ub=inequality,
        b_ub=np.zeros(count),
        A_eq=equality,
        b_eq=np.array([1.0, 0.0]),
        bounds=[(0.0, 1.0)] * count + [(0.0, None)],
        method="highs",
    )
    if not result.success or result.x is None or float(result.x[-1]) <= 0.0:
        return None
    spans = np.asarray(result.x[:count], dtype=float)
    return spans if np.all(spans > 0.0) else None


def build_mca_mean_line(
    inlet_metal_angle_deg: float,
    outlet_metal_angle_deg: float,
    node_angles_deg: tuple[float, ...] | list[float],
) -> ArcMeanLineSpec:
    """由完整节点角序列构造多圆弧中弧线，自动给出严格可行的内节点站位。

    给定节点切线角 ``phi_0 > ... > phi_N``（度）后，弦向跨度 ``delta`` 只在
    ``sum(delta) = 1``、``sum(delta tan mu) = 0``、``delta > 0`` 的仿射可行域内自由。
    本函数先用启发式初值 ``delta_i ∝ 1 / (2 sin(Δ_i/2) cos mu_i)``，再用两条等式约束
    解出末两段（前 ``N-2`` 段保持初值）。该初值**既不是等弧长解也不是等半径解**
    （隐含半径是权重的二次函数），而且只对**近似等步长**的节点角族通常可行。

    一般节点角序列下该初值可能给出非正跨度，此时**并不代表不可行**：本函数会退回到
    「最大化最小跨度」的线性规划，在仿射可行域非空时给出一个严格为正的解。只有当
    可行域本身为空（不存在全部为正的跨度）才抛 ``ValueError``。最小反例：
    节点角 ``(60, 30, 0, -30)`` 的权重初值是 ``[0.405827, -0.460198, 1.054371]``，
    而可行解存在（例如显式站位 ``0.211283``）。

    若需要复现某个特定的跨度分布，请显式给出 ``interior_node_stations``。
    """

    angles = np.asarray(node_angles_deg, dtype=float)
    if angles.size < 4:
        raise ValueError("多圆弧至少需要 4 个节点角")
    if np.any(np.diff(angles) >= 0.0):
        raise ValueError("节点切线角必须严格递减")

    radians = np.radians(angles)
    tan_mu = np.tan(0.5 * (radians[:-1] + radians[1:]))
    delta = _weight_span_initial(radians)
    if not np.all(delta > 0.0):
        repaired = _maximin_span_distribution(tan_mu)
        if repaired is None:
            # 判据是精确的：``δ > 0`` 且 ``δ·tanμ = 0`` 要求 0 落在 ``tanμ`` 的严格
            # 凸包内部，即 ``min tanμ < 0 < max tanμ``。线性规划因容差失败时，显式内点
            # 仍然存在，因此只有两条路径都失败才真的不可行。
            repaired = _analytic_positive_span_distribution(tan_mu)
        if repaired is None:
            raise ValueError(
                "节点角序列的仿射可行域为空（不存在全部为正的弦向跨度），"
                f"请改用显式 interior_node_stations: delta={np.round(delta, 6).tolist()}"
            )
        delta = repaired
    stations = np.cumsum(delta)[:-1]
    if not np.all((stations > 0.0) & (stations < 1.0)):
        raise ValueError("内节点站位未落在 (0, 1)")
    return ArcMeanLineSpec(
        family=ArcFamily.MULTIPLE,
        inlet_metal_angle_deg=float(angles[0]),
        outlet_metal_angle_deg=float(-angles[-1]),
        interior_node_angles_deg=tuple(float(value) for value in angles[1:-1]),
        interior_node_stations=tuple(float(value) for value in stations[:-1]),
    )


def build_designation(spec: ArcProfileSpec) -> str:
    """人类可读型号名，例如 ``DCA χ1=30.0°/χ2=20.0° a=0.600 t/c=0.100``。"""

    mean = spec.mean_line
    family_label = {
        ArcFamily.SINGLE: "CA",
        ArcFamily.DOUBLE: "DCA",
        ArcFamily.MULTIPLE: f"MCA{mean.arc_count}",
    }[mean.family]
    return (
        f"{family_label} χ1={mean.inlet_metal_angle_deg:.1f}°"
        f"/χ2={mean.outlet_metal_angle_deg:.1f}°"
        f" a={mean.camber_fraction_le:.3f} t/c={spec.max_thickness_ratio:.3f}"
    )
