from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Handedness, _round_floats

SCHEMA_VERSION_V2 = "2.0.0"
GENERATOR_VERSION_V2 = "multi-curve-normal-offset-v2"
COORDINATE_CONVENTION_V2 = "closed-chord:x-le-nose-to-te-nose,y-to-suction"


class CurveFamily(StrEnum):
    BEZIER = "bezier"
    BSPLINE = "bspline"
    NURBS = "nurbs"


class BezierCurveSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    family: Literal[CurveFamily.BEZIER] = CurveFamily.BEZIER
    degree: int = Field(ge=1, le=12)
    control_points: list[list[float]]

    @model_validator(mode="after")
    def check_shape(self) -> BezierCurveSpec:
        _check_control_points(self.control_points)
        if len(self.control_points) != self.degree + 1:
            raise ValueError("Bezier 控制点数量必须等于 degree + 1")
        return self


class BSplineCurveSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    family: Literal[CurveFamily.BSPLINE] = CurveFamily.BSPLINE
    degree: int = Field(default=3, ge=1, le=5)
    control_points: list[list[float]]
    knots: list[float]

    @model_validator(mode="after")
    def check_shape(self) -> BSplineCurveSpec:
        _check_spline(self.control_points, self.knots, self.degree)
        return self


class NURBSCurveSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    family: Literal[CurveFamily.NURBS] = CurveFamily.NURBS
    degree: int = Field(default=3, ge=1, le=5)
    control_points: list[list[float]]
    knots: list[float]
    weights: list[float]

    @model_validator(mode="after")
    def check_shape(self) -> NURBSCurveSpec:
        _check_spline(self.control_points, self.knots, self.degree)
        if len(self.weights) != len(self.control_points):
            raise ValueError("NURBS 权重数量必须与控制点一致")
        if not all(np.isfinite(self.weights)) or any(weight <= 0 for weight in self.weights):
            raise ValueError("NURBS 权重必须为有限正数")
        if any(weight < 0.25 or weight > 4.0 for weight in self.weights):
            raise ValueError("NURBS 高级权重必须位于 [0.25, 4]")
        return self


CurveSpec = Annotated[
    BezierCurveSpec | BSplineCurveSpec | NURBSCurveSpec,
    Field(discriminator="family"),
]


class EdgeSpec(BaseModel):
    model_config = ConfigDict(frozen=True)
    leading_edge_radius_ratio: float = Field(ge=0.0005, le=0.03)
    trailing_edge_radius_ratio: float = Field(ge=0.0001, le=0.02)
    trailing_edge_half_wedge_deg: float = Field(ge=0.0, le=25.0)


class GeometryIntentV2(BaseModel):
    model_config = ConfigDict(frozen=True)
    inlet_metal_angle_deg: float = Field(ge=20.0, le=75.0)
    turning_angle_deg: float = Field(ge=0.0, le=65.0)
    quarter_angle_bias_deg: float = Field(default=0.0, ge=-12.0, le=12.0)
    three_quarter_angle_bias_deg: float = Field(default=0.0, ge=-12.0, le=12.0)
    max_thickness_ratio: float = Field(ge=0.03, le=0.18)
    max_thickness_x_ratio: float = Field(ge=0.20, le=0.65)
    leading_edge_radius_ratio: float = Field(ge=0.0005, le=0.03)
    trailing_edge_radius_ratio: float = Field(ge=0.0001, le=0.02)
    trailing_edge_half_wedge_deg: float = Field(default=8.0, ge=0.0, le=25.0)
    fore_shape_factor: float = Field(default=1.0, ge=0.5, le=1.8)
    aft_shape_factor: float = Field(default=1.0, ge=0.5, le=1.8)
    handedness: Handedness = Handedness.RIGHT

    @model_validator(mode="after")
    def check_outlet(self) -> GeometryIntentV2:
        if not -20.0 <= self.outlet_metal_angle_deg <= 65.0:
            raise ValueError("出口金属角必须位于 [-20, 65] deg")
        if 2 * self.leading_edge_radius_ratio >= self.max_thickness_ratio:
            raise ValueError("前缘直径必须小于最大厚度")
        if 2 * self.trailing_edge_radius_ratio >= self.max_thickness_ratio:
            raise ValueError("尾缘直径必须小于最大厚度")
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
    generator_version: str = GENERATOR_VERSION_V2
    coordinate_convention: str = COORDINATE_CONVENTION_V2
    authoring_mode: Literal["basic", "advanced"] = "basic"
    intent: GeometryIntentV2
    camber_family: CurveFamily = CurveFamily.BEZIER
    thickness_family: CurveFamily = CurveFamily.BSPLINE
    linked_thickness: bool = True
    camber_curve: CurveSpec | None = None
    suction_thickness_curve: CurveSpec | None = None
    pressure_thickness_curve: CurveSpec | None = None
    edge: EdgeSpec | None = None
    points_per_side: int = Field(default=201, ge=51, le=1001)
    resolution_residuals: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_explicit_contract(self) -> GeometrySpecV2:
        if self.authoring_mode == "advanced" and not self.resolved:
            raise ValueError("高级模式必须提供完整显式曲线与边缘合同")
        if self.camber_curve and self.camber_curve.family != self.camber_family:
            raise ValueError("camber_curve 与 camber_family 不一致")
        if (
            self.suction_thickness_curve
            and self.suction_thickness_curve.family != self.thickness_family
        ):
            raise ValueError("厚度曲线与 thickness_family 不一致")
        if self.linked_thickness and self.resolved:
            if self.pressure_thickness_curve != self.suction_thickness_curve:
                raise ValueError("联动厚度要求吸力面与压力面曲线完全一致")
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
    leading_edge_arc: list[list[float]]
    trailing_edge_arc: list[list[float]]
    realized_stagger_deg: float
    edge_metrics: dict[str, float]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def arrays(
        self,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.asarray(self.suction, dtype=float),
            np.asarray(self.pressure, dtype=float),
            np.asarray(self.camber, dtype=float),
            np.asarray(self.thickness, dtype=float),
        )


def _check_control_points(points: list[list[float]]) -> None:
    if len(points) < 2:
        raise ValueError("曲线至少需要两个控制点")
    dimensions = {len(point) for point in points}
    if len(dimensions) != 1 or next(iter(dimensions)) not in {1, 2}:
        raise ValueError("控制点必须统一为一维或二维")
    if not np.all(np.isfinite(np.asarray(points, dtype=float))):
        raise ValueError("控制点必须为有限数")


def _check_spline(points: list[list[float]], knots: list[float], degree: int) -> None:
    _check_control_points(points)
    if len(points) < degree + 1:
        raise ValueError("样条控制点数量不足")
    if len(knots) != len(points) + degree + 1:
        raise ValueError("节点数量必须等于控制点数 + degree + 1")
    if not all(np.isfinite(knots)) or any(b < a for a, b in zip(knots, knots[1:], strict=False)):
        raise ValueError("节点必须有限且非递减")
    if knots[degree] >= knots[-degree - 1]:
        raise ValueError("样条有效参数区间退化")
    if len(set(knots[: degree + 1])) != 1 or len(set(knots[-degree - 1 :])) != 1:
        raise ValueError("v2 只接受开放夹持样条")
