from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION_V2 = "2.1.0"
GENERATOR_VERSION_V2 = "unified-bspline-g2-v2.1"
COORDINATE_CONVENTION_V2 = (
    "intrinsic-chord:x-le-nose-to-te-nose,y-to-suction;placement:x-axial,y-positive-tangential"
)


class Handedness(StrEnum):
    RIGHT = "right"
    LEFT = "left"


class ValidationStatus(StrEnum):
    VALID = "valid"
    WARNING = "warning"
    REJECTED = "rejected"


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    value: float | str | None = None
    limit: float | str | None = None


class ValidationReport(BaseModel):
    geometry_id: str
    status: ValidationStatus
    rule_version: str = "geometry-rules-v2"
    issues: list[ValidationIssue] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)

    @property
    def valid(self) -> bool:
        return self.status != ValidationStatus.REJECTED


class CascadeContext(BaseModel):
    solidity: float = Field(ge=0.7, le=2.2)
    handedness: Handedness = Handedness.RIGHT


class SectionDefinition(BaseModel):
    span_fraction: float = Field(ge=0.0, le=1.0)
    radius_m: float = Field(gt=0.0)
    chord_m: float = Field(gt=0.0)
    geometry_id: str
    stagger_deg: float
    axial_offset_m: float = 0.0
    tangential_offset_m: float = 0.0
    lean_m: float = 0.0
    sweep_m: float = 0.0


class SectionSchedule(BaseModel):
    name: str
    blade_count: int = Field(gt=0)
    rotation_direction: Literal["cw", "ccw"] = "ccw"
    sections: list[SectionDefinition] = Field(min_length=2)
    output_section_count: int | None = Field(default=None, ge=2, le=201)
    hub_curve_path: str | None = None
    shroud_curve_path: str | None = None
    units: Literal["m"] = "m"

    @model_validator(mode="after")
    def check_order(self) -> SectionSchedule:
        spans = [section.span_fraction for section in self.sections]
        radii = [section.radius_m for section in self.sections]
        if spans != sorted(spans) or len(set(spans)) != len(spans):
            raise ValueError("截面 span_fraction 必须严格递增")
        if any(b <= a for a, b in zip(radii, radii[1:], strict=False)):
            raise ValueError("截面 radius_m 必须严格递增")
        return self


class BladeSection(BaseModel):
    span_fraction: float
    radius_m: float
    suction_xyz: list[list[float]]
    pressure_xyz: list[list[float]]
    geometry_id: str
    interpolated: bool = False


class BladeSurface(BaseModel):
    blade_id: str
    schedule: SectionSchedule
    sections: list[BladeSection]
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    warnings: list[str] = Field(default_factory=list)


class MetalAngleReference(StrEnum):
    AXIAL = "axial"
    TANGENTIAL = "tangential"


def axial_to_tangential_angle(angle_deg: float) -> float:
    """BladeGen-compatible complementary beta representation."""

    return 90.0 - angle_deg


def tangential_to_axial_angle(angle_deg: float) -> float:
    return 90.0 - angle_deg


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
    inlet_metal_angle_deg: float = Field(ge=-84.0, le=174.0)
    outlet_metal_angle_deg: float = Field(ge=-84.0, le=174.0)
    metal_angle_reference: MetalAngleReference = MetalAngleReference.AXIAL
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
        axial_angles = (self.inlet_metal_angle_axial_deg, self.outlet_metal_angle_axial_deg)
        if any(not -84.0 <= angle <= 84.0 for angle in axial_angles):
            raise ValueError("轴向参考的进口和出口金属角必须位于 [-84, 84] deg")
        if 2 * self.leading_edge_radius_ratio >= self.max_thickness_ratio:
            raise ValueError("前缘直径必须小于最大厚度")
        if 2 * self.trailing_edge_radius_ratio >= self.max_thickness_ratio:
            raise ValueError("尾缘直径必须小于最大厚度")
        local_angles = (
            self.inlet_metal_angle_axial_deg - self.stagger_deg,
            self.outlet_metal_angle_axial_deg - self.stagger_deg,
        )
        if any(abs(angle) >= 85.0 for angle in local_angles):
            raise ValueError("金属角相对安装角必须位于 (-85, 85) deg")
        return self

    @property
    def inlet_metal_angle_axial_deg(self) -> float:
        if self.metal_angle_reference == MetalAngleReference.AXIAL:
            return self.inlet_metal_angle_deg
        return tangential_to_axial_angle(self.inlet_metal_angle_deg)

    @property
    def outlet_metal_angle_axial_deg(self) -> float:
        if self.metal_angle_reference == MetalAngleReference.AXIAL:
            return self.outlet_metal_angle_deg
        return tangential_to_axial_angle(self.outlet_metal_angle_deg)

    @property
    def inlet_metal_angle_tangential_deg(self) -> float:
        return axial_to_tangential_angle(self.inlet_metal_angle_axial_deg)

    @property
    def outlet_metal_angle_tangential_deg(self) -> float:
        return axial_to_tangential_angle(self.outlet_metal_angle_axial_deg)

    @property
    def inlet_local_angle_deg(self) -> float:
        return round(self.inlet_metal_angle_axial_deg - self.stagger_deg, 12)

    @property
    def outlet_local_angle_deg(self) -> float:
        return round(self.outlet_metal_angle_axial_deg - self.stagger_deg, 12)

    def normalized_axial(self) -> GeometryIntentV2:
        """Persist signed axial angles; the input reference is only a representation."""

        return self.model_copy(
            update={
                "inlet_metal_angle_deg": round(self.inlet_metal_angle_axial_deg, 12),
                "outlet_metal_angle_deg": round(self.outlet_metal_angle_axial_deg, 12),
                "metal_angle_reference": MetalAngleReference.AXIAL,
            }
        )

    @property
    def metal_angle_difference_deg(self) -> float:
        return self.inlet_metal_angle_axial_deg - self.outlet_metal_angle_axial_deg

    @property
    def intent_id(self) -> str:
        # Reference and stagger are presentation/placement.  The intent identity is
        # the intrinsic section shape in the chord frame.
        payload_data = self.model_dump(
            mode="json", exclude={"metal_angle_reference", "stagger_deg"}
        )
        payload_data.pop("inlet_metal_angle_deg")
        payload_data.pop("outlet_metal_angle_deg")
        payload_data.update(
            {
                "inlet_local_angle_deg": self.inlet_local_angle_deg,
                "outlet_local_angle_deg": self.outlet_local_angle_deg,
            }
        )
        payload = json.dumps(
            _round_floats(payload_data),
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return "intent_" + hashlib.sha256(payload).hexdigest()[:20]


class GeometrySpecV2(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["2.1.0"] = SCHEMA_VERSION_V2
    generator_version: Literal["unified-bspline-g2-v2.1"] = GENERATOR_VERSION_V2
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
    schema_version: Literal["2.1.0"] = SCHEMA_VERSION_V2
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
    schema_version: Literal["2.1.0"] = SCHEMA_VERSION_V2
    geometry_id: str
    intent_id: str
    kernel: Literal["unified_bspline_g2"] = "unified_bspline_g2"
    inlet_metal_angle_deg: float
    outlet_metal_angle_deg: float
    inlet_metal_angle_axial_deg: float
    outlet_metal_angle_axial_deg: float
    inlet_metal_angle_tangential_deg: float
    outlet_metal_angle_tangential_deg: float
    inlet_local_angle_deg: float
    outlet_local_angle_deg: float
    metal_angle_difference_deg: float
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


class DatasetManifestV2(BaseModel):
    dataset_name: str = "geometry-v2.1.0"
    schema_version: Literal["2.1.0"] = SCHEMA_VERSION_V2
    generator_version: Literal["unified-bspline-g2-v2.1"] = GENERATOR_VERSION_V2
    seed: int = 20260901
    target_valid: int
    generated_valid: int
    anchors: int = 0
    anchor_fits: int = 0
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


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_sha256(payload: Any) -> str:
    raw = json.dumps(_round_floats(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _round_floats(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 12)
    if isinstance(value, dict):
        return {key: _round_floats(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_floats(item) for item in value]
    return value
