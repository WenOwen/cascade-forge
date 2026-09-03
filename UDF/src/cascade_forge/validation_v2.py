from __future__ import annotations

import math

import numpy as np

from .curves import CurveEvaluator
from .features import extract_features
from .models import CascadeContext, ValidationIssue, ValidationReport, ValidationStatus
from .models_v2 import AirfoilGeometryV2, GeometryFeaturesV2


def validate_profile_v2(
    geometry: AirfoilGeometryV2,
    cascade_context: CascadeContext | None = None,
    *,
    features: GeometryFeaturesV2 | None = None,
) -> ValidationReport:
    suction, pressure, camber, thickness = geometry.arrays()
    issues: list[ValidationIssue] = []

    def add(severity: str, code: str, message: str, value=None, limit=None) -> None:
        issues.append(
            ValidationIssue(
                code=code,
                severity=severity,
                message=message,
                value=value,
                limit=limit,
            )
        )

    expected = geometry.spec.points_per_side
    if not (len(suction) == len(pressure) == len(camber) == len(thickness) == expected):
        add("error", "array_length", "v2 坐标数组未满足每侧定长合同", len(suction), expected)
    if not all(np.all(np.isfinite(array)) for array in (suction, pressure, camber, thickness)):
        add("error", "non_finite", "坐标或厚度包含非有限值")

    closure = max(
        float(np.linalg.norm(suction[0] - pressure[0])),
        float(np.linalg.norm(suction[-1] - pressure[-1])),
    )
    if closure > 1e-10:
        add("error", "outline_closure", "前缘或尾缘鼻点未闭合", closure, 1e-10)
    minimum_thickness = float(np.min(thickness[1:-1]))
    if minimum_thickness <= 0.0:
        add("error", "non_positive_thickness", "叶型内部存在非正厚度", minimum_thickness, ">0")

    features = features or extract_features(geometry)
    if features.full_outline_self_intersects:
        add("error", "outline_self_intersection", "完整闭合轮廓发生自交")
    if abs(features.signed_area) <= 1e-10:
        add("error", "polygon_area", "叶型多边形面积退化", features.signed_area, "abs(area)>1e-10")

    normal_rms = geometry.edge_metrics["normal_fit_rms"]
    normal_max = geometry.edge_metrics["normal_fit_max"]
    if normal_rms > 2e-4:
        add("error", "normal_fit_rms", "约束叶面偏离法向厚度目标", normal_rms, 2e-4)
    if normal_max > 1e-3:
        add("error", "normal_fit_max", "约束叶面最大法向目标误差超限", normal_max, 1e-3)

    assert geometry.spec.edge is not None
    for name, target_radius in (
        ("leading", geometry.spec.edge.leading_edge_radius_ratio),
        ("trailing", geometry.spec.edge.trailing_edge_radius_ratio),
    ):
        actual_radius = geometry.edge_metrics[f"{name}_edge_radius_ratio"]
        radius_error = abs(actual_radius - target_radius)
        relative_error = geometry.edge_metrics[f"{name}_radius_relative_error"]
        if radius_error > 1e-8 or relative_error > 1e-6:
            add(
                "error",
                f"{name}_osculating_radius",
                "鼻点密切圆半径误差超限",
                max(radius_error, relative_error),
                "abs<=1e-8 and rel<=1e-6",
            )
        tangency = geometry.edge_metrics[f"{name}_tangency_error_deg"]
        normal_tangency = geometry.edge_metrics[f"{name}_camber_normal_error_deg"]
        if max(tangency, normal_tangency) > 0.05:
            add(
                "error",
                f"{name}_g2_tangent",
                "吸压面鼻点切线未满足 G2 方向合同",
                max(tangency, normal_tangency),
                0.05,
            )
    center_error = geometry.edge_metrics["osculating_center_error"]
    if center_error > 1e-8:
        add("error", "osculating_center", "双侧密切圆圆心不一致", center_error, 1e-8)

    target_tmax = geometry.spec.intent.max_thickness_ratio
    target_xt = geometry.spec.intent.max_thickness_x_ratio
    tmax_error = abs(features.max_thickness_ratio - target_tmax)
    xt_error = abs(features.max_thickness_x_ratio - target_xt)
    if geometry.spec.authoring_mode == "basic":
        if tmax_error > 5e-4:
            add("error", "tmax_mismatch", "最大厚度未达到工程意图", tmax_error, 5e-4)
        if xt_error > 0.005:
            add("error", "xt_mismatch", "最大厚度位置未达到工程意图", xt_error, 0.005)

    inlet_actual, outlet_actual = _actual_metal_angles(geometry)
    inlet_target = geometry.spec.intent.inlet_metal_angle_axial_deg
    outlet_target = geometry.spec.intent.outlet_metal_angle_axial_deg
    inlet_error = abs(inlet_actual - inlet_target)
    outlet_error = abs(outlet_actual - outlet_target)
    if inlet_error > 0.05:
        add("error", "inlet_metal_angle", "进口金属角误差超限", inlet_error, 0.05)
    if outlet_error > 0.05:
        add("error", "outlet_metal_angle", "出口金属角误差超限", outlet_error, 0.05)

    metrics = {
        "closure_error": closure,
        "minimum_thickness_ratio": minimum_thickness,
        "normal_fit_rms": normal_rms,
        "normal_fit_max": normal_max,
        "leading_edge_radius_error": abs(
            features.leading_edge_radius_ratio - geometry.spec.edge.leading_edge_radius_ratio
        ),
        "trailing_edge_radius_error": abs(
            features.trailing_edge_radius_ratio - geometry.spec.edge.trailing_edge_radius_ratio
        ),
        "leading_tangency_error_deg": geometry.edge_metrics["leading_tangency_error_deg"],
        "trailing_tangency_error_deg": geometry.edge_metrics["trailing_tangency_error_deg"],
        "osculating_center_error": center_error,
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
        add(
            "warning",
            "curvature_gradient",
            "叶面曲率梯度偏高",
            metrics["max_abs_curvature_gradient"],
            50000.0,
        )

    if cascade_context is not None:
        pitch = 1.0 / cascade_context.solidity
        throat_proxy = pitch - features.max_thickness_ratio
        metrics["cascade_pitch_ratio"] = pitch
        metrics["cascade_throat_proxy_ratio"] = throat_proxy
        if throat_proxy <= 0.0:
            add("error", "cascade_interference", "相邻叶片发生干涉", throat_proxy, ">0")
        elif throat_proxy < 0.03:
            add("warning", "narrow_throat", "叶栅喉道裕度很小", throat_proxy, 0.03)

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
        rule_version="geometry-rules-v2-g2",
        issues=issues,
        metrics=metrics,
    )


def _actual_metal_angles(geometry: AirfoilGeometryV2) -> tuple[float, float]:
    assert geometry.spec.camber_curve is not None
    evaluator = CurveEvaluator(geometry.spec.camber_curve)
    derivatives = evaluator.evaluate([0.0, 1.0], 1)
    local = [math.degrees(math.atan2(vector[1], vector[0])) for vector in derivatives]
    return (
        local[0] + geometry.realized_stagger_deg,
        local[1] + geometry.realized_stagger_deg,
    )
