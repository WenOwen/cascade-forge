from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Handedness, _round_floats

SCHEMA_VERSION_V2 = "2.0.0"
GENERATOR_VERSION_V2 = "unified-bspline-g2-v2"
COORDINATE_CONVENTION_V2 = "closed-chord:x-le-nose-to-te-nose,y-to-suction"


class BSplineCurveSpec(BaseModel):
    """统一内核使用的三次开放夹持 B-spline 显式合同。"""

    model_config = ConfigDict(frozen=True)
    degree: Literal[3] = 3
    control_points: list[list[float]]
    knots: list[float]

    @model_validator(mode="after")
    def check_shape(self) -> BSplineCurveSpec:
        _check_spline(self.control_points, self.knots, self.degree)
        return self


class EdgeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    leading_edge_radius_ratio: float = Field(ge=0.0005, le=0.03)
    trailing_edge_radius_ratio: float = Field(ge=0.0001, le=0.02)


class GeometryIntentV2(BaseModel):
    """基础模式工程变量；显式 B-spline 才是 Geometry ID 的真值。"""

    model_config = ConfigDict(frozen=True)
    inlet_metal_angle_deg: float = Field(ge=-84.0, le=84.0)
    turning_angle_deg: float = Field(ge=-168.0, le=168.0)
    stagger_deg: float = Field(default=42.0, ge=-84.0, le=84.0)
    camber_le_tangent_ratio: float = Field(default=0.33, ge=0.03, le=1.2)
    camber_te_tangent_ratio: float = Field(default=0.33, ge=0.03, le=1.2)
    max_thickness_ratio: float = Field(ge=0.03, le=0.18)
    max_thickness_x_ratio: float = Field(ge=0.20, le=0.65)
    leading_edge_radius_ratio: float = Field(ge=0.0005, le=0.03)
    trailing_edge_radius_ratio: float = Field(ge=0.0001, le=0.02)
    fore_shape_factor: float = Field(default=1.0, ge=0.5, le=1.8)
    aft_shape_factor: float = Field(default=1.0, ge=0.5, le=1.8)
    handedness: Handedness = Handedness.RIGHT

    @model_validator(mode="after")
    def check_geometry_domain(self) -> GeometryIntentV2:
        if not -84.0 <= self.outlet_metal_angle_deg <= 84.0:
            raise ValueError("出口金属角必须位于 [-84, 84] deg")
        if 2 * self.leading_edge_radius_ratio >= self.max_thickness_ratio:
            raise ValueError("前缘直径必须小于最大厚度")
        if 2 * self.trailing_edge_radius_ratio >= self.max_thickness_ratio:
            raise ValueError("尾缘直径必须小于最大厚度")
        local_angles = (
            self.inlet_metal_angle_deg - self.stagger_deg,
            self.outlet_metal_angle_deg - self.stagger_deg,
        )
        if any(abs(angle) >= 85.0 for angle in local_angles):
            raise ValueError("金属角相对安装角必须位于 (-85, 85) deg")
        return self

    @property
    def outlet_metal_angle_deg(self) -> float:
        return self.inlet_metal_angle_deg - self.turning_angle_deg

    @property
    def intent_id(self) -> str:
        payload = json.dumps(
            _round_floats(self.model_dump(mode="json")),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return "intent_" + hashlib.sha256(payload).hexdigest()[:20]


class GeometrySpecV2(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION_V2
    generator_version: Literal["unified-bspline-g2-v2"] = GENERATOR_VERSION_V2
    coordinate_convention: str = COORDINATE_CONVENTION_V2
    authoring_mode: Literal["basic", "advanced"] = "basic"
    intent: GeometryIntentV2
    linked_thickness: bool = True
    camber_curve: BSplineCurveSpec | None = None
    suction_thickness_curve: BSplineCurveSpec | None = None
    pressure_thickness_curve: BSplineCurveSpec | None = None
    edge: EdgeSpec | None = None
    points_per_side: int = Field(default=201, ge=51, le=1001)
    surface_fit_start: float = Field(default=0.08, ge=0.02, le=0.20)
    surface_fit_end: float = Field(default=0.92, ge=0.80, le=0.98)
    surface_fit_samples: int = Field(default=321, ge=101, le=2001)
    surface_fit_regularization: float = Field(default=1e-6, gt=0.0, le=1e-2)
    resolution_residuals: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_explicit_contract(self) -> GeometrySpecV2:
        if self.surface_fit_start >= self.surface_fit_end:
            raise ValueError("叶面拟合参数区间无效")
        if self.authoring_mode == "advanced" and not self.resolved:
            raise ValueError("高级模式必须提供完整显式 B-spline 与边缘合同")
        if self.linked_thickness and self.resolved:
            if self.pressure_thickness_curve != self.suction_thickness_curve:
                raise ValueError("联动厚度要求吸力面与压力面曲线完全一致")
        for name, curve, expected in (
            ("camber", self.camber_curve, 4),
            ("suction_thickness", self.suction_thickness_curve, 8),
            ("pressure_thickness", self.pressure_thickness_curve, 8),
        ):
            if curve is not None and len(curve.control_points) != expected:
                raise ValueError(f"{name} 控制点数量必须为 {expected}")
        return self

    @property
    def resolved(self) -> bool:
        return all(
            item is not None
            for item in (
                self.camber_curve,
                self.suction_thickness_curve,
                self.pressure_thickness_curve,
                self.edge,
            )
        )

    def canonical_payload(self) -> dict[str, Any]:
        if not self.resolved:
            raise ValueError("未解析的基础 spec 不能计算 Geometry ID")
        return _round_floats(
            {
                "schema_version": self.schema_version,
                "generator_version": self.generator_version,
                "coordinate_convention": self.coordinate_convention,
                "camber_curve": self.camber_curve.model_dump(mode="json"),
                "suction_thickness_curve": self.suction_thickness_curve.model_dump(mode="json"),
                "pressure_thickness_curve": self.pressure_thickness_curve.model_dump(mode="json"),
                "edge": self.edge.model_dump(mode="json"),
                "points_per_side": self.points_per_side,
                "surface_fit_start": self.surface_fit_start,
                "surface_fit_end": self.surface_fit_end,
                "surface_fit_samples": self.surface_fit_samples,
                "surface_fit_regularization": self.surface_fit_regularization,
            }
        )

    @property
    def geometry_id(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode()
        return "geo_v2_" + hashlib.sha256(raw).hexdigest()[:24]


class AirfoilGeometryV2(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION_V2
    geometry_id: str
    intent_id: str
    spec: GeometrySpecV2
    origin: Literal["generated", "anchor_fit", "workspace"] = "generated"
    source_id: str | None = None
    suction: list[list[float]]
    pressure: list[list[float]]
    camber: list[list[float]]
    thickness: list[float]
    normals: list[list[float]]
    suction_surface_curve: BSplineCurveSpec
    pressure_surface_curve: BSplineCurveSpec
    leading_osculating_circle: list[list[float]]
    trailing_osculating_circle: list[list[float]]
    realized_stagger_deg: float
    edge_metrics: dict[str, float]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.asarray(self.suction, dtype=float),
            np.asarray(self.pressure, dtype=float),
            np.asarray(self.camber, dtype=float),
            np.asarray(self.thickness, dtype=float),
        )


class GeometryFeaturesV2(BaseModel):
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION_V2
    geometry_id: str
    intent_id: str
    kernel: Literal["unified_bspline_g2"] = "unified_bspline_g2"
    inlet_metal_angle_deg: float
    outlet_metal_angle_deg: float
    turning_angle_deg: float
    stagger_deg: float
    max_thickness_ratio: float
    max_thickness_x_ratio: float
    max_camber_ratio: float
    max_camber_x_ratio: float
    leading_edge_radius_ratio: float
    trailing_edge_radius_ratio: float
    suction_max_abs_curvature: float
    pressure_max_abs_curvature: float
    max_abs_curvature_gradient: float
    leading_edge_max_abs_curvature: float
    trailing_edge_max_abs_curvature: float
    signed_area: float
    full_outline_self_intersects: bool


class AnchorFitRecord(BaseModel):
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION_V2
    fit_id: str
    source_anchor_id: str
    geometry_id: str | None = None
    rms_distance_ratio: float
    max_distance_ratio: float
    hausdorff_approx_ratio: float
    fit_status: Literal["valid", "warning", "rejected"]
    iterations: int = 0
    converged: bool = False
    termination_reason: str = "not_started"
    source_warnings: list[str] = Field(default_factory=list)
    explicit_spec: dict[str, Any]


class DatasetManifestV2(BaseModel):
    dataset_name: str = "geometry-v2.0.0"
    schema_version: Literal["2.0.0"] = SCHEMA_VERSION_V2
    generator_version: Literal["unified-bspline-g2-v2"] = GENERATOR_VERSION_V2
    seed: int = 20260901
    target_valid: int
    generated_valid: int
    anchors: int = 48
    anchor_fits: int = 48
    anchor_gate_passed: bool = False
    proposals: int
    rejected_proposals: int
    acceptance_rate: float
    config_sha256: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    files: dict[str, str] = Field(default_factory=dict)
    batch_manifests: list[str] = Field(default_factory=list)
    rejection_distribution: dict[str, int] = Field(default_factory=dict)
    provenance: dict[str, str] = Field(default_factory=dict)


def _check_control_points(points: list[list[float]]) -> None:
    if len(points) < 4:
        raise ValueError("三次 B-spline 至少需要四个控制点")
    dimensions = {len(point) for point in points}
    if len(dimensions) != 1 or next(iter(dimensions)) not in {1, 2}:
        raise ValueError("控制点必须统一为一维或二维")
    if not np.all(np.isfinite(np.asarray(points, dtype=float))):
        raise ValueError("控制点必须为有限数")


def _check_spline(points: list[list[float]], knots: list[float], degree: int) -> None:
    _check_control_points(points)
    if len(knots) != len(points) + degree + 1:
        raise ValueError("节点数量必须等于控制点数 + degree + 1")
    if not all(np.isfinite(knots)) or any(b < a for a, b in zip(knots, knots[1:], strict=False)):
        raise ValueError("节点必须有限且非递减")
    if knots[degree] >= knots[-degree - 1]:
        raise ValueError("样条有效参数区间退化")
    if len(set(knots[: degree + 1])) != 1 or len(set(knots[-degree - 1 :])) != 1:
        raise ValueError("统一内核只接受开放夹持样条")
