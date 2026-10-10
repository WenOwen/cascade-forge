from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cascade_forge.domain.models import BSplineCurveSpec, _check_spline, _round_floats

NACA65_SCHEMA_VERSION = "1.0.0"
NACA65_ALGORITHM_VERSION = "nasa-sixseries-1974-python-v1"
NACA65_BENCHMARK_VERSION = "naca65-benchmark-3.0.0"


class NACA65Family(StrEnum):
    COMPRESSOR_65 = "65-compressor"
    SERIES_65 = "65"
    SERIES_65A = "65A"


class NACA65InputMode(StrEnum):
    GENERAL = "general"
    COMPRESSOR_PRESET = "compressor_preset"


class NACA65Spec(BaseModel):
    """Native NASA sixseries input contract for the 65/65A families."""

    model_config = ConfigDict(frozen=True)
    schema_version: Literal["1.0.0"] = NACA65_SCHEMA_VERSION
    algorithm_version: Literal["nasa-sixseries-1974-python-v1", "naca65-compressor-table-v1"] = (
        NACA65_ALGORITHM_VERSION
    )
    family: NACA65Family = NACA65Family.SERIES_65
    thickness_ratio: float = Field(default=0.10, ge=0.03, le=0.18)
    design_lift_coefficient: float = Field(default=0.8, ge=0.0, le=2.0)
    loading_parameter_a: float = Field(default=0.8, ge=0.0, le=1.0)
    input_mode: NACA65InputMode = NACA65InputMode.GENERAL
    preset_designation: str | None = None
    points_per_side: Literal[201] = 201
    decomposition_version: Literal["sixseries-stations-v2"] = "sixseries-stations-v2"

    @model_validator(mode="before")
    @classmethod
    def compressor_defaults(cls, values):
        if isinstance(values, dict) and values.get("family") == "65-compressor":
            values = dict(values)
            values.setdefault("loading_parameter_a", 1.0)
            values["algorithm_version"] = "naca65-compressor-table-v1"
        return values

    @model_validator(mode="after")
    def check_family_contract(self) -> NACA65Spec:
        if self.family == NACA65Family.COMPRESSOR_65 and self.loading_parameter_a != 1.0:
            raise ValueError("NACA 65 压气机系列的中弧线固定为 a=1.0")
        if (
            self.family != NACA65Family.COMPRESSOR_65
            and self.algorithm_version != NACA65_ALGORITHM_VERSION
        ):
            raise ValueError("压气机算法版本只能用于 65-compressor")
        if self.family == NACA65Family.SERIES_65A:
            if abs(self.loading_parameter_a - 0.8) > 1e-12:
                raise ValueError("NACA 65A 的载荷参数 a 固定为 0.8")
            if self.input_mode == NACA65InputMode.COMPRESSOR_PRESET:
                raise ValueError("压气机编号预设仅映射标准 NACA 65，不虚构 65A 别名")
        return self

    @property
    def canonical_designation(self) -> str:
        cli_tenths = int(round(self.design_lift_coefficient * 10.0))
        thickness_percent = int(round(self.thickness_ratio * 100.0))
        if self.family == NACA65Family.COMPRESSOR_65:
            lift = str(cli_tenths) if cli_tenths <= 9 else f"({cli_tenths})"
            return f"NACA 65-{lift}{thickness_percent:02d} compressor · rounded TE"
        if self.family == NACA65Family.SERIES_65A:
            return f"NACA 65A-{cli_tenths}{thickness_percent:02d} a=0.8 mod"
        if cli_tenths <= 9:
            return f"NACA 65-{cli_tenths}{thickness_percent:02d} a={self.loading_parameter_a:.1f}"
        return f"NACA 65-({cli_tenths}){thickness_percent:02d} a={self.loading_parameter_a:.1f}"

    @property
    def native_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"input_mode", "preset_designation"})
        raw = json.dumps(_round_floats(payload), sort_keys=True, separators=(",", ":")).encode()
        return "naca65_" + hashlib.sha256(raw).hexdigest()[:24]


class NACA65Geometry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    schema_version: Literal["1.0.0"] = NACA65_SCHEMA_VERSION
    native_id: str
    designation: str
    spec: NACA65Spec
    raw_suction: list[list[float]]
    raw_pressure: list[list[float]]
    normalized_suction: list[list[float]]
    normalized_pressure: list[list[float]]
    camber: list[list[float]]
    thickness: list[float]
    construction_camber: list[list[float]]
    construction_half_thickness: list[float]
    construction_normals: list[list[float]]
    construction_parameter: list[float]
    parameter: list[float]
    source_provenance: dict[str, str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class NACA65FitReport(BaseModel):
    schema_version: Literal["1.0.0"] = NACA65_SCHEMA_VERSION
    fit_id: str
    native_id: str
    geometry_id: str | None = None
    fit_status: Literal["valid", "warning", "rejected", "failed"]
    rms_distance_ratio: float | None
    max_distance_ratio: float | None
    hausdorff_approx_ratio: float | None
    core_rms_distance_ratio: float | None
    core_max_distance_ratio: float | None
    suction_signed_normal_error: list[float]
    pressure_signed_normal_error: list[float]
    area_difference_ratio: float | None
    feature_deltas: dict[str, float]
    iterations: int
    converged: bool
    termination_reason: str
    elapsed_seconds: float
    explicit_spec: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    diagnostics: dict[str, float] = Field(default_factory=dict)
    error_parameter: list[float] = Field(default_factory=list)
    method_version: str = "naca65-normal-bspline-v3"


class NACA65CamberCurveSpec(BaseModel):
    """C4 quintic mean line: its normal field is C3 at simple knots."""

    model_config = ConfigDict(frozen=True)
    degree: Literal[5] = 5
    control_points: list[list[float]]
    knots: list[float]

    @model_validator(mode="after")
    def check_shape(self) -> NACA65CamberCurveSpec:
        _check_spline(self.control_points, self.knots, self.degree)
        if len(self.control_points) < 6 or any(len(point) != 2 for point in self.control_points):
            raise ValueError("NACA 五次中弧线至少需要六个二维控制点")
        interior = self.knots[6:-6]
        if len(set(interior)) != len(interior):
            raise ValueError("NACA 中弧线仅允许简单内节点，以保证法向叶面曲率连续")
        return self


class NACA65SplineSpec(BaseModel):
    """Authoritative normal-offset model, not a v2.1 surface approximation."""

    generator_version: Literal["naca65-normal-bspline-v3", "naca65-compressor-bspline-v1"] = (
        "naca65-normal-bspline-v3"
    )
    camber_curve: NACA65CamberCurveSpec
    thickness_shape_curve: BSplineCurveSpec
    linked_thickness: Literal[True] = True
    thickness_law: Literal["sqrt(s)*(1-s)*B(s)", "sqrt(s)*B(s)"] = "sqrt(s)*(1-s)*B(s)"
    parameter_convention: str = "normalized-source-mean-line-arclength"
    trailing_edge: Literal["sharp", "rounded"] = "sharp"
    trailing_edge_radius_ratio: float = Field(default=0.0, ge=0.0, le=0.01)
    source_decomposition: str = "sixseries-mean-line-normal-stations"
    camber_fairness_weight: float = 2e-8
    thickness_fairness_weight: float = 1e-8

    @model_validator(mode="after")
    def check_tail_contract(self):
        rounded = self.trailing_edge == "rounded"
        if rounded != (self.generator_version == "naca65-compressor-bspline-v1"):
            raise ValueError("圆尾缘需要独立压气机生成器版本")
        if rounded != (self.thickness_law == "sqrt(s)*B(s)"):
            raise ValueError("尾缘类型与厚度公式不一致")
        if (rounded and self.trailing_edge_radius_ratio <= 0) or (
            not rounded and self.trailing_edge_radius_ratio != 0
        ):
            raise ValueError("圆尾缘半径必须为正，尖尾缘半径必须为零")
        return self

    @property
    def geometry_id(self) -> str:
        raw = json.dumps(
            self.model_dump(
                mode="json",
                exclude={
                    "source_decomposition",
                    "camber_fairness_weight",
                    "thickness_fairness_weight",
                    *(set() if self.trailing_edge == "rounded" else {"trailing_edge_radius_ratio"}),
                },
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        return "geo_naca_normal_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


class NACA65FitGeometry(BaseModel):
    geometry_id: str
    source_id: str
    spec: NACA65SplineSpec
    suction: list[list[float]]
    pressure: list[list[float]]
    camber: list[list[float]]
    thickness: list[float]
    normals: list[list[float]]
    parameter: list[float]
    leading_osculating_circle: list[list[float]]
    trailing_osculating_circle: list[list[float]] = Field(default_factory=list)
    edge_metrics: dict[str, float]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def arrays(self):
        import numpy as np

        return tuple(
            np.asarray(x) for x in (self.suction, self.pressure, self.camber, self.thickness)
        )


class NACA65BenchmarkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["1.0.0"] = NACA65_SCHEMA_VERSION
    benchmark_version: Literal["naca65-benchmark-3.0.0"] = NACA65_BENCHMARK_VERSION
    compressor: bool = False
    thickness_ratios: tuple[float, ...] = (0.06, 0.10, 0.15)
    design_lift_coefficients: tuple[float, ...] = (0.0, 0.8, 1.2)
    series_65_loading_parameters: tuple[float, ...] = (0.6, 0.8, 1.0)
    series_65a_loading_parameter: Literal[0.8] = 0.8
    max_outer_iterations: int = Field(default=6, ge=1, le=20)
    max_design_evaluations: int = Field(default=80, ge=10, le=500)

    @property
    def case_count(self) -> int:
        if self.compressor:
            return len(self.thickness_ratios) * len(self.design_lift_coefficients)
        return (
            len(self.thickness_ratios)
            * len(self.design_lift_coefficients)
            * (len(self.series_65_loading_parameters) + 1)
        )


class NACA65BenchmarkManifest(BaseModel):
    schema_version: Literal["1.0.0"] = NACA65_SCHEMA_VERSION
    benchmark_version: Literal["naca65-benchmark-3.0.0"] = NACA65_BENCHMARK_VERSION
    run_id: str
    config: NACA65BenchmarkConfig
    total_cases: int
    completed_cases: int
    status_distribution: dict[str, int]
    source_provenance: dict[str, str]
    files: dict[str, str]
    created_at: str
    completed_at: str | None = None
