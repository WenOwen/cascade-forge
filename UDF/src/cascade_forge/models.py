from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0.0"
GENERATOR_VERSION = "angle-spline-c2-thickness-v1"
COORDINATE_CONVENTION = "chord-local:x-le-to-te,y-to-suction"


class Handedness(StrEnum):
    RIGHT = "right"
    LEFT = "left"


class ValidationStatus(StrEnum):
    VALID = "valid"
    WARNING = "warning"
    REJECTED = "rejected"


class GeometrySpec(BaseModel):
    """能够唯一重建二维叶型的无量纲生成参数。"""

    model_config = ConfigDict(frozen=True)

    inlet_metal_angle_deg: float = Field(ge=20.0, le=75.0)
    turning_angle_deg: float = Field(ge=0.0, le=65.0)
    quarter_angle_bias_deg: float = Field(ge=-12.0, le=12.0)
    three_quarter_angle_bias_deg: float = Field(ge=-12.0, le=12.0)
    max_thickness_ratio: float = Field(ge=0.02, le=0.15)
    max_thickness_x_ratio: float = Field(ge=0.20, le=0.65)
    leading_edge_radius_ratio: float = Field(ge=0.001, le=0.015)
    trailing_edge_thickness_ratio: float = Field(ge=0.001, le=0.020)
    trailing_edge_half_wedge_deg: float = Field(ge=2.0, le=15.0)
    fore_shape_factor: float = Field(ge=0.6, le=1.6)
    aft_shape_factor: float = Field(ge=0.6, le=1.6)
    handedness: Handedness = Handedness.RIGHT
    points_per_side: int = Field(default=201, ge=51, le=1001)
    generator_version: str = GENERATOR_VERSION
    coordinate_convention: str = COORDINATE_CONVENTION

    @model_validator(mode="after")
    def check_outlet_angle(self) -> GeometrySpec:
        outlet = self.inlet_metal_angle_deg - self.turning_angle_deg
        if not -20.0 <= outlet <= 65.0:
            raise ValueError("出口金属角必须位于 [-20, 65] deg")
        return self

    @property
    def outlet_metal_angle_deg(self) -> float:
        return self.inlet_metal_angle_deg - self.turning_angle_deg

    def canonical_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        return _round_floats(payload)

    @property
    def geometry_id(self) -> str:
        raw = json.dumps(self.canonical_payload(), sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        return "geo_" + hashlib.sha256(raw).hexdigest()[:24]


class AirfoilGeometry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    geometry_id: str
    spec: GeometrySpec | None = None
    origin: Literal["generated", "anchor"] = "generated"
    source_id: str | None = None
    suction: list[list[float]]
    pressure: list[list[float]]
    camber: list[list[float]]
    thickness: list[float]
    realized_stagger_deg: float
    metadata: dict[str, Any] = Field(default_factory=dict)

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        return (
            np.asarray(self.suction, dtype=float),
            np.asarray(self.pressure, dtype=float),
            np.asarray(self.camber, dtype=float),
            np.asarray(self.thickness, dtype=float),
        )


class GeometryFeatures(BaseModel):
    geometry_id: str
    inlet_metal_angle_deg: float
    outlet_metal_angle_deg: float
    turning_angle_deg: float
    stagger_deg: float
    max_thickness_ratio: float
    max_thickness_x_ratio: float
    max_camber_ratio: float
    max_camber_x_ratio: float
    leading_edge_radius_ratio: float
    trailing_edge_thickness_ratio: float
    trailing_edge_half_wedge_deg: float
    suction_max_abs_curvature: float
    pressure_max_abs_curvature: float
    max_abs_curvature_gradient: float
    signed_area: float


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["warning", "error"]
    message: str
    value: float | str | None = None
    limit: float | str | None = None


class ValidationReport(BaseModel):
    geometry_id: str
    status: ValidationStatus
    rule_version: str = "geometry-rules-v1"
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


class DatasetManifest(BaseModel):
    dataset_name: str
    schema_version: str = SCHEMA_VERSION
    generator_version: str = GENERATOR_VERSION
    seed: int
    target_valid: int
    generated_valid: int
    anchors: int
    rejected: int
    config_sha256: str
    created_at: str
    files: dict[str, str]
    provenance: dict[str, str]


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
