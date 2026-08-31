from __future__ import annotations

import math

import numpy as np

from .curves import CurveEvaluator
from .features import extract_features
from .models import (
    CascadeContext,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
)
from .models_v2 import AirfoilGeometryV2, GeometryFeaturesV2, NURBSCurveSpec


def validate_profile_v2(
    geometry: AirfoilGeometryV2,
    cascade_context: CascadeContext | None = None,
    *,
    features: GeometryFeaturesV2 | None = None,
) -> ValidationReport:
    suction, pressure, camber, thickness = geometry.arrays()
    issues: list[ValidationIssue] = []

    def error(code: str, message: str, value=None, limit=None) -> None:
        issues.append(
            ValidationIssue(
                code=code,
                severity="error",
                message=message,
                value=value,
                limit=limit,
            )
        )

    def warning(code: str, message: str, value=None, limit=None) -> None:
        issues.append(
            ValidationIssue(
                code=code,
                severity="warning",
                message=message,
                value=value,
                limit=limit,
            )
        )

    expected = geometry.spec.points_per_side
    if not (len(suction) == len(pressure) == len(camber) == len(thickness) == expected):
        error("array_length", "v2 坐标数组未满足每侧定长合同", len(suction), expected)
    if not all(np.all(np.isfinite(array)) for array in (suction, pressure, camber, thickness)):
        error("non_finite", "坐标或厚度包含非有限值")

    closure = max(
        float(np.linalg.norm(suction[0] - pressure[0])),
        float(np.linalg.norm(suction[-1] - pressure[-1])),
    )
    if closure > 1e-10:
        error("outline_closure", "前缘或尾缘鼻点未闭合", closure, 1e-10)

    minimum_thickness = float(np.min(thickness[1:-1]))
    if minimum_thickness <= 0.0:
        error("non_positive_thickness", "中央曲面存在非正厚度", minimum_thickness, ">0")

    features = features or extract_features(geometry)
    if features.full_outline_self_intersects:
        error("outline_self_intersection", "完整闭合轮廓发生自交")
    if abs(features.signed_area) <= 1e-10:
        error("polygon_area", "叶型多边形面积退化", features.signed_area, "abs(area)>1e-10")

    normal_error = geometry.edge_metrics["normal_offset_error"]
    if normal_error > 1e-10:
        error("normal_offset", "中央曲面未严格沿中弧线法向叠加", normal_error, 1e-10)

    edge_checks = (
        ("leading", geometry.spec.edge.leading_edge_radius_ratio),
        ("trailing", geometry.spec.edge.trailing_edge_radius_ratio),
    )
    for name, target_radius in edge_checks:
        actual_radius = geometry.edge_metrics[f"{name}_edge_radius_ratio"]
        radius_error = abs(actual_radius - target_radius)
        if radius_error > 1e-8:
            error(f"{name}_radius", "真实圆弧半径误差超限", radius_error, 1e-8)
        tangency = geometry.edge_metrics[f"{name}_tangency_error_deg"]
        if tangency > 0.05:
            error(f"{name}_g1", "圆弧与中央曲面 G1 相切误差超限", tangency, 0.05)

    for curve_name, curve in (
        ("camber", geometry.spec.camber_curve),
        ("suction_thickness", geometry.spec.suction_thickness_curve),
        ("pressure_thickness", geometry.spec.pressure_thickness_curve),
    ):
        if isinstance(curve, NURBSCurveSpec):
            lower = 0.5 if geometry.spec.authoring_mode == "basic" else 0.25
            upper = 2.0 if geometry.spec.authoring_mode == "basic" else 4.0
            if min(curve.weights) < lower or max(curve.weights) > upper:
                error(
                    f"{curve_name}_weights",
                    "NURBS 权重超出当前编辑模式合同",
                    f"[{min(curve.weights):.6g}, {max(curve.weights):.6g}]",
                    f"[{lower}, {upper}]",
                )

    target_tmax = geometry.spec.intent.max_thickness_ratio
    tmax_error = abs(features.max_thickness_ratio - target_tmax)
    if tmax_error > 5e-4:
        error("tmax_mismatch", "最大厚度未达到工程意图", tmax_error, 5e-4)
    target_xt = geometry.spec.intent.max_thickness_x_ratio
    xt_error = abs(features.max_thickness_x_ratio - target_xt)
    if xt_error > 0.005:
        error("xt_mismatch", "最大厚度位置未达到工程意图", xt_error, 0.005)

    inlet_actual, outlet_actual = _actual_metal_angles(geometry)
    sign = 1.0 if geometry.spec.intent.handedness.value == "right" else -1.0
    inlet_target = sign * geometry.spec.intent.inlet_metal_angle_deg
    outlet_target = sign * geometry.spec.intent.outlet_metal_angle_deg
    inlet_error = abs(inlet_actual - inlet_target)
    outlet_error = abs(outlet_actual - outlet_target)
    if inlet_error > 0.05:
        error("inlet_metal_angle", "进口金属角误差超限", inlet_error, 0.05)
    if outlet_error > 0.05:
        error("outlet_metal_angle", "出口金属角误差超限", outlet_error, 0.05)

    metrics = {
        "closure_error": closure,
        "minimum_thickness_ratio": minimum_thickness,
        "normal_offset_error": normal_error,
        "leading_edge_radius_error": abs(
            features.leading_edge_radius_ratio - geometry.spec.edge.leading_edge_radius_ratio
        ),
        "trailing_edge_radius_error": abs(
            features.trailing_edge_radius_ratio - geometry.spec.edge.trailing_edge_radius_ratio
        ),
        "leading_tangency_error_deg": geometry.edge_metrics["leading_tangency_error_deg"],
        "trailing_tangency_error_deg": geometry.edge_metrics["trailing_tangency_error_deg"],
        "max_thickness_ratio": features.max_thickness_ratio,
        "max_thickness_x_ratio": features.max_thickness_x_ratio,
        "max_thickness_error": tmax_error,
        "max_thickness_x_error": xt_error,
        "inlet_metal_angle_error_deg": inlet_error,
        "outlet_metal_angle_error_deg": outlet_error,
        "signed_area": features.signed_area,
        "max_abs_curvature": max(
            features.suction_max_abs_curvature,
            features.pressure_max_abs_curvature,
        ),
        "max_abs_curvature_gradient": features.max_abs_curvature_gradient,
    }

    if metrics["max_abs_curvature_gradient"] > 50000.0:
        warning(
            "curvature_gradient",
            "中央曲面曲率梯度偏高",
            metrics["max_abs_curvature_gradient"],
            50000.0,
        )

    if cascade_context is not None:
        pitch = 1.0 / cascade_context.solidity
        throat_proxy = pitch - features.max_thickness_ratio
        metrics["cascade_pitch_ratio"] = pitch
        metrics["cascade_throat_proxy_ratio"] = throat_proxy
        if throat_proxy <= 0.0:
            error("cascade_interference", "相邻叶片发生干涉", throat_proxy, ">0")
        elif throat_proxy < 0.03:
            warning("narrow_throat", "叶栅喉道裕度很小", throat_proxy, 0.03)

    status = (
        ValidationStatus.REJECTED
        if any(issue.severity == "error" for issue in issues)
        else ValidationStatus.WARNING
        if issues
        else ValidationStatus.VALID
    )
    return ValidationReport(
        geometry_id=geometry.geometry_id,
        status=status,
        rule_version="geometry-rules-v2",
        issues=issues,
        metrics=metrics,
    )


def _actual_metal_angles(geometry: AirfoilGeometryV2) -> tuple[float, float]:
    evaluator = CurveEvaluator(geometry.spec.camber_curve)
    derivatives = evaluator.evaluate([0.0, 1.0], 1)
    rotation = float(geometry.metadata["construction_rotation_deg"])
    local = [math.degrees(math.atan2(vector[1], vector[0])) - rotation for vector in derivatives]
    return (
        local[0] + geometry.realized_stagger_deg,
        local[1] + geometry.realized_stagger_deg,
    )
