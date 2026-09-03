from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import _round_floats

NACA65_SCHEMA_VERSION = "1.0.0"
NACA65_ALGORITHM_VERSION = "nasa-sixseries-1974-python-v1"
NACA65_BENCHMARK_VERSION = "naca65-benchmark-1.0.0"


class NACA65Family(StrEnum):
    SERIES_65 = "65"
    SERIES_65A = "65A"


class NACA65InputMode(StrEnum):
    GENERAL = "general"
    COMPRESSOR_PRESET = "compressor_preset"


class NACA65Spec(BaseModel):
    """Native NASA sixseries input contract for the 65/65A families."""

    model_config = ConfigDict(frozen=True)
    schema_version: Literal["1.0.0"] = NACA65_SCHEMA_VERSION
    algorithm_version: Literal["nasa-sixseries-1974-python-v1"] = NACA65_ALGORITHM_VERSION
    family: NACA65Family = NACA65Family.SERIES_65
    thickness_ratio: float = Field(default=0.10, ge=0.03, le=0.18)
    design_lift_coefficient: float = Field(default=0.8, ge=0.0, le=2.0)
    loading_parameter_a: float = Field(default=0.8, ge=0.0, le=1.0)
    input_mode: NACA65InputMode = NACA65InputMode.GENERAL
    preset_designation: str | None = None
    points_per_side: Literal[201] = 201

    @model_validator(mode="after")
    def check_family_contract(self) -> NACA65Spec:
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
        if self.family == NACA65Family.SERIES_65A:
            return f"NACA 65A-{cli_tenths}{thickness_percent:02d} a=0.8 mod"
        if cli_tenths <= 9:
            return f"NACA 65-{cli_tenths}{thickness_percent:02d} a={self.loading_parameter_a:.1f}"
        return f"NACA 65-({cli_tenths}){thickness_percent:02d} a={self.loading_parameter_a:.1f}"

    @property
    def native_id(self) -> str:
        payload = self.model_dump(
            mode="json", exclude={"input_mode", "preset_designation"}
        )
        raw = json.dumps(
            _round_floats(payload), sort_keys=True, separators=(",", ":")
        ).encode()
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


class NACA65BenchmarkConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    schema_version: Literal["1.0.0"] = NACA65_SCHEMA_VERSION
    benchmark_version: Literal["naca65-benchmark-1.0.0"] = NACA65_BENCHMARK_VERSION
    thickness_ratios: tuple[float, ...] = (0.06, 0.10, 0.15)
    design_lift_coefficients: tuple[float, ...] = (0.0, 0.8, 1.2)
    series_65_loading_parameters: tuple[float, ...] = (0.6, 0.8, 1.0)
    series_65a_loading_parameter: Literal[0.8] = 0.8
    max_outer_iterations: int = Field(default=6, ge=1, le=20)
    max_design_evaluations: int = Field(default=80, ge=10, le=500)

    @property
    def case_count(self) -> int:
        return (
            len(self.thickness_ratios)
            * len(self.design_lift_coefficients)
            * (len(self.series_65_loading_parameters) + 1)
        )


class NACA65BenchmarkManifest(BaseModel):
    schema_version: Literal["1.0.0"] = NACA65_SCHEMA_VERSION
    benchmark_version: Literal["naca65-benchmark-1.0.0"] = NACA65_BENCHMARK_VERSION
    run_id: str
    config: NACA65BenchmarkConfig
    total_cases: int
    completed_cases: int
    status_distribution: dict[str, int]
    source_provenance: dict[str, str]
    files: dict[str, str]
    created_at: str
    completed_at: str | None = None
