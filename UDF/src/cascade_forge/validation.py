from __future__ import annotations

import numpy as np

from .features import extract_features
from .models import (
    AirfoilGeometry,
    CascadeContext,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
)
from .models_v2 import AirfoilGeometryV2


def validate_profile(
    geometry: AirfoilGeometry | AirfoilGeometryV2,
    cascade_context: CascadeContext | None = None,
) -> ValidationReport:
    if getattr(geometry, "schema_version", None) in {"2.0.0", "2.1.0"}:
        from .validation_v2 import validate_profile_v2

        return validate_profile_v2(geometry, cascade_context)
    suction, pressure, camber, thickness = geometry.arrays()
    issues: list[ValidationIssue] = []

    def error(code: str, message: str, value=None, limit=None) -> None:
        issues.append(
            ValidationIssue(code=code, severity="error", message=message, value=value, limit=limit)
        )

    def warning(code: str, message: str, value=None, limit=None) -> None:
        issues.append(
            ValidationIssue(
                code=code, severity="warning", message=message, value=value, limit=limit
            )
        )

    if not (len(suction) == len(pressure) == len(camber) == len(thickness)):
        error("array_length", "坐标数组长度不一致")
    if not all(np.all(np.isfinite(array)) for array in (suction, pressure, camber, thickness)):
        error("non_finite", "坐标或厚度包含非有限值")
    if len(camber) < 3:
        error("too_few_points", "坐标点数不足", len(camber), 3)
        return _report(geometry, issues, {})

    closure = max(abs(camber[0, 1]), abs(camber[-1, 1]), np.linalg.norm(suction[0] - pressure[0]))
    if closure > 1e-10:
        error("leading_edge_closure", "前缘或弦线闭合误差超限", closure, 1e-10)

    if np.any(np.diff(camber[:, 0]) <= 0.0):
        error("non_monotonic_x", "弦向坐标必须严格递增")

    interior = thickness[1:]
    minimum_gap = float(np.min(interior)) if len(interior) else -1.0
    if minimum_gap <= 1e-7:
        error("non_positive_thickness", "叶型厚度非正或过小", minimum_gap, 1e-7)
    if np.any(suction[:, 1] < pressure[:, 1] - 1e-12):
        error("surface_crossing", "吸力面与压力面发生交叉")

    features = extract_features(geometry)
    if abs(features.signed_area) <= 1e-10:
        error("polygon_area", "叶型多边形面积退化", features.signed_area, "abs(area)>1e-10")
    if features.max_camber_ratio > 0.42:
        error("excessive_camber", "最大弯度超出生成域", features.max_camber_ratio, 0.42)
    # Curvature and its gradient are diagnostics, not universal rejection criteria.

    if geometry.spec is not None:
        thickness_error = abs(features.max_thickness_ratio - geometry.spec.max_thickness_ratio)
        if thickness_error > 5e-4:
            error("tmax_mismatch", "最大厚度未达到目标", thickness_error, 5e-4)
        x_error = abs(features.max_thickness_x_ratio - geometry.spec.max_thickness_x_ratio)
        if x_error > 0.01:
            warning("xt_discretization", "最大厚度位置受离散化影响", x_error, 0.01)

    if geometry.metadata.get("out_of_generation_domain"):
        warning("out_of_generation_domain", "锚点超出 v1 生成域")
    for source_warning in geometry.metadata.get("source_warnings", []):
        warning("source_data", source_warning)

    metrics = {
        "closure_error": float(closure),
        "minimum_surface_gap_ratio": minimum_gap,
        "signed_area": features.signed_area,
        "max_thickness_ratio": features.max_thickness_ratio,
        "max_thickness_x_ratio": features.max_thickness_x_ratio,
        "max_camber_ratio": features.max_camber_ratio,
        "max_abs_curvature": max(
            features.suction_max_abs_curvature, features.pressure_max_abs_curvature
        ),
        "max_abs_curvature_gradient": features.max_abs_curvature_gradient,
    }

    if cascade_context is not None:
        pitch = 1.0 / cascade_context.solidity
        throat_proxy = pitch - float(np.max(thickness))
        metrics["cascade_pitch_ratio"] = pitch
        metrics["cascade_throat_proxy_ratio"] = throat_proxy
        if throat_proxy <= 0.0:
            error("cascade_interference", "相邻叶片发生干涉", throat_proxy, ">0")
        elif throat_proxy < 0.03:
            warning("narrow_throat", "叶栅喉道裕度很小", throat_proxy, 0.03)

    return _report(geometry, issues, metrics)


def _report(
    geometry: AirfoilGeometry,
    issues: list[ValidationIssue],
    metrics: dict[str, float],
) -> ValidationReport:
    if any(issue.severity == "error" for issue in issues):
        status = ValidationStatus.REJECTED
    elif issues:
        status = ValidationStatus.WARNING
    else:
        status = ValidationStatus.VALID
    return ValidationReport(
        geometry_id=geometry.geometry_id,
        status=status,
        rule_version="geometry-rules-v1-curvature-diagnostic",
        issues=issues,
        metrics=metrics,
    )
