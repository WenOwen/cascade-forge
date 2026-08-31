from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares, lsq_linear

from .curves import (
    CurveEvaluator,
    angle_between_lines,
    open_uniform_knots,
    unit_tangent_and_normal,
)
from .geometry2d import _generate_profile_v1
from .models import GeometrySpec, Handedness
from .models_v2 import (
    AirfoilGeometryV2,
    BezierCurveSpec,
    BSplineCurveSpec,
    CurveFamily,
    EdgeSpec,
    GeometrySpecV2,
    NURBSCurveSpec,
)


@dataclass
class _EdgeSolution:
    center: np.ndarray
    suction_u: float
    pressure_u: float
    suction_point: np.ndarray
    pressure_point: np.ndarray
    arc: np.ndarray
    nose_index: int
    center_residual: float
    tangency_error_deg: float


def resolve_geometry_spec(spec: GeometrySpecV2) -> GeometrySpecV2:
    """把基础工程意图确定性解析为显式曲线合同。"""

    if spec.resolved:
        return spec
    intent = spec.intent
    reference = _generate_profile_v1(
        GeometrySpec(
            inlet_metal_angle_deg=intent.inlet_metal_angle_deg,
            turning_angle_deg=intent.turning_angle_deg,
            quarter_angle_bias_deg=intent.quarter_angle_bias_deg,
            three_quarter_angle_bias_deg=intent.three_quarter_angle_bias_deg,
            max_thickness_ratio=intent.max_thickness_ratio,
            max_thickness_x_ratio=intent.max_thickness_x_ratio,
            leading_edge_radius_ratio=min(intent.leading_edge_radius_ratio, 0.015),
            trailing_edge_thickness_ratio=max(
                0.001, min(2 * intent.trailing_edge_radius_ratio, 0.02)
            ),
            trailing_edge_half_wedge_deg=min(15.0, max(2.0, intent.trailing_edge_half_wedge_deg)),
            fore_shape_factor=intent.fore_shape_factor,
            aft_shape_factor=intent.aft_shape_factor,
            handedness=intent.handedness,
            points_per_side=max(201, spec.points_per_side),
        )
    )
    _, _, reference_camber, _ = reference.arrays()
    target_u = np.linspace(0.0, 1.0, len(reference_camber))
    camber_curve, camber_error = _resolve_camber(spec.camber_family, target_u, reference_camber)
    half_target = _reference_half_thickness(spec)
    thickness_curve, thickness_error = _resolve_thickness(
        spec.thickness_family, target_u, half_target
    )
    edge = EdgeSpec(
        leading_edge_radius_ratio=intent.leading_edge_radius_ratio,
        trailing_edge_radius_ratio=intent.trailing_edge_radius_ratio,
        trailing_edge_half_wedge_deg=intent.trailing_edge_half_wedge_deg,
    )
    return spec.model_copy(
        update={
            "camber_curve": camber_curve,
            "suction_thickness_curve": thickness_curve,
            "pressure_thickness_curve": thickness_curve,
            "edge": edge,
            "resolution_residuals": {
                "camber_rms": camber_error,
                "half_thickness_rms": thickness_error,
            },
        }
    )


def generate_profile_v2(spec: GeometrySpecV2) -> AirfoilGeometryV2:
    resolved = resolve_geometry_spec(spec)
    assert resolved.camber_curve is not None
    assert resolved.suction_thickness_curve is not None
    assert resolved.pressure_thickness_curve is not None
    assert resolved.edge is not None

    dense_u = np.linspace(0.0, 1.0, max(1201, resolved.points_per_side * 6))
    camber_eval = CurveEvaluator(resolved.camber_curve)
    suction_thickness_eval = CurveEvaluator(resolved.suction_thickness_curve)
    pressure_thickness_eval = CurveEvaluator(resolved.pressure_thickness_curve)
    camber = camber_eval.evaluate(dense_u)
    tangent, normal = unit_tangent_and_normal(camber_eval, dense_u)
    suction_half = suction_thickness_eval.evaluate(dense_u)[:, 0]
    pressure_half = pressure_thickness_eval.evaluate(dense_u)[:, 0]
    if np.any(suction_half < 0) or np.any(pressure_half < 0):
        raise ValueError("法向半厚度不能为负")
    suction_raw = camber + suction_half[:, None] * normal
    pressure_raw = camber - pressure_half[:, None] * normal

    leading_radius = resolved.edge.leading_edge_radius_ratio
    trailing_radius = resolved.edge.trailing_edge_radius_ratio
    for _ in range(12):
        leading = _solve_edge_circle(
            camber_eval,
            suction_thickness_eval,
            pressure_thickness_eval,
            leading_radius,
            leading=True,
        )
        trailing = _solve_edge_circle(
            camber_eval,
            suction_thickness_eval,
            pressure_thickness_eval,
            trailing_radius,
            leading=False,
        )
        construction_chord = float(
            np.linalg.norm(trailing.arc[trailing.nose_index] - leading.arc[leading.nose_index])
        )
        next_leading = resolved.edge.leading_edge_radius_ratio * construction_chord
        next_trailing = resolved.edge.trailing_edge_radius_ratio * construction_chord
        if (
            max(
                abs(next_leading - leading_radius),
                abs(next_trailing - trailing_radius),
            )
            <= 1e-13
        ):
            break
        leading_radius = next_leading
        trailing_radius = next_trailing
    suction_mask = (dense_u >= leading.suction_u) & (dense_u <= trailing.suction_u)
    pressure_mask = (dense_u >= leading.pressure_u) & (dense_u <= trailing.pressure_u)
    if np.count_nonzero(suction_mask) < 10 or np.count_nonzero(pressure_mask) < 10:
        raise ValueError("边缘圆弧裁剪后中央曲面点数不足")

    le_suction = leading.arc[leading.nose_index :]
    le_pressure = leading.arc[: leading.nose_index + 1][::-1]
    te_suction = trailing.arc[: trailing.nose_index + 1]
    te_pressure = trailing.arc[trailing.nose_index :][::-1]
    suction_path = np.vstack([le_suction[:-1], suction_raw[suction_mask], te_suction[1:]])
    pressure_path = np.vstack([le_pressure[:-1], pressure_raw[pressure_mask], te_pressure[1:]])
    suction = _resample_polyline(suction_path, resolved.points_per_side)
    pressure = _resample_polyline(pressure_path, resolved.points_per_side)

    le_nose = 0.5 * (suction[0] + pressure[0])
    te_nose = 0.5 * (suction[-1] + pressure[-1])
    chord_vector = te_nose - le_nose
    chord = float(np.linalg.norm(chord_vector))
    if chord <= 1e-8:
        raise ValueError("最终鼻点弦长退化")
    rotation = (
        np.array([[chord_vector[0], chord_vector[1]], [-chord_vector[1], chord_vector[0]]]) / chord
    )

    def normalize(points: np.ndarray) -> np.ndarray:
        return (points - le_nose) @ rotation.T / chord

    suction = normalize(suction)
    pressure = normalize(pressure)
    sample_beta = np.linspace(0.0, math.pi, resolved.points_per_side)
    sample_u = 0.5 * (1.0 - np.cos(sample_beta))
    sampled_camber = camber_eval.evaluate(sample_u)
    sampled_tangent, sampled_normal = unit_tangent_and_normal(camber_eval, sample_u)
    sampled_suction_half = suction_thickness_eval.evaluate(sample_u)[:, 0]
    sampled_pressure_half = pressure_thickness_eval.evaluate(sample_u)[:, 0]
    camber_normalized = normalize(sampled_camber)
    le_arc = normalize(leading.arc)
    te_arc = normalize(trailing.arc)
    normals_rotated = sampled_normal @ rotation.T
    thickness = (sampled_suction_half + sampled_pressure_half) / chord
    sign = 1.0 if resolved.intent.handedness == Handedness.RIGHT else -1.0
    transformed_start_tangent = sampled_tangent[0] @ rotation.T
    local_start_angle = math.degrees(
        math.atan2(transformed_start_tangent[1], transformed_start_tangent[0])
    )
    realized_stagger = sign * resolved.intent.inlet_metal_angle_deg - local_start_angle

    return AirfoilGeometryV2(
        geometry_id=resolved.geometry_id,
        intent_id=resolved.intent.intent_id,
        spec=resolved,
        suction=suction.tolist(),
        pressure=pressure.tolist(),
        camber=camber_normalized.tolist(),
        thickness=thickness.tolist(),
        normals=normals_rotated.tolist(),
        leading_edge_arc=le_arc.tolist(),
        trailing_edge_arc=te_arc.tolist(),
        realized_stagger_deg=realized_stagger,
        edge_metrics={
            "leading_edge_radius_ratio": leading_radius / chord,
            "trailing_edge_radius_ratio": trailing_radius / chord,
            "leading_center_residual": leading.center_residual / chord,
            "trailing_center_residual": trailing.center_residual / chord,
            "leading_tangency_error_deg": leading.tangency_error_deg,
            "trailing_tangency_error_deg": trailing.tangency_error_deg,
            "normal_offset_error": _normal_offset_error(
                camber, normal, suction_raw, pressure_raw, suction_half, pressure_half
            )
            / chord,
        },
        metadata={
            "mathematical_kernel": "multi-curve + normal-offset + tangent-circle-edges",
            "camber_family": resolved.camber_family.value,
            "thickness_family": resolved.thickness_family.value,
            "construction_chord_ratio": chord,
            "leading_edge_center": normalize(leading.center[None, :])[0].tolist(),
            "trailing_edge_center": normalize(trailing.center[None, :])[0].tolist(),
        },
    )


def reference_stagger(spec: GeometrySpecV2) -> float:
    assert spec.camber_curve is not None
    evaluator = CurveEvaluator(spec.camber_curve)
    tangent = evaluator.evaluate(np.array([0.0]), 1)[0]
    local_angle = math.degrees(math.atan2(tangent[1], tangent[0]))
    return spec.intent.inlet_metal_angle_deg - local_angle


def _resolve_camber(family: CurveFamily, target_u: np.ndarray, target: np.ndarray):
    theta_start = math.atan2(target[1, 1] - target[0, 1], target[1, 0] - target[0, 0])
    theta_end = math.atan2(target[-1, 1] - target[-2, 1], target[-1, 0] - target[-2, 0])
    start_direction = np.array([math.cos(theta_start), math.sin(theta_start)])
    end_direction = np.array([math.cos(theta_end), math.sin(theta_end)])
    if family == CurveFamily.BEZIER:

        def build(values):
            h1, h2 = values
            return BezierCurveSpec(
                degree=3,
                control_points=[
                    [0.0, 0.0],
                    (h1 * start_direction).tolist(),
                    (np.array([1.0, 0.0]) - h2 * end_direction).tolist(),
                    [1.0, 0.0],
                ],
            )

        result = least_squares(
            lambda values: (CurveEvaluator(build(values)).evaluate(target_u) - target).ravel(),
            [0.32, 0.32],
            bounds=([0.03, 0.03], [1.2, 1.2]),
            xtol=1e-12,
            ftol=1e-12,
            gtol=1e-12,
        )
        curve = build(result.x)
    else:
        knots = open_uniform_knots(6, 3)
        weights = [1.0, 0.92, 1.08, 1.12, 0.95, 1.0] if family == CurveFamily.NURBS else None

        def build(values):
            h1, h2, p2x, p2y, p3x, p3y = values
            points = [
                [0.0, 0.0],
                (h1 * start_direction).tolist(),
                [p2x, p2y],
                [p3x, p3y],
                (np.array([1.0, 0.0]) - h2 * end_direction).tolist(),
                [1.0, 0.0],
            ]
            if weights is None:
                return BSplineCurveSpec(degree=3, control_points=points, knots=knots)
            return NURBSCurveSpec(degree=3, control_points=points, knots=knots, weights=weights)

        p2 = target[len(target) // 3]
        p3 = target[2 * len(target) // 3]
        initial = [0.25, 0.25, p2[0], p2[1], p3[0], p3[1]]
        result = least_squares(
            lambda values: (CurveEvaluator(build(values)).evaluate(target_u) - target).ravel(),
            initial,
            bounds=(
                [0.02, 0.02, 0.05, -0.6, 0.30, -0.6],
                [1.0, 1.0, 0.70, 0.6, 0.95, 0.6],
            ),
            xtol=1e-11,
            ftol=1e-11,
            gtol=1e-11,
        )
        curve = build(result.x)
    error = float(np.sqrt(np.mean((CurveEvaluator(curve).evaluate(target_u) - target) ** 2)))
    return curve, error


def _reference_half_thickness(spec: GeometrySpecV2) -> np.ndarray:
    intent = spec.intent
    u = np.linspace(0.0, 1.0, max(201, spec.points_per_side))
    xt = intent.max_thickness_x_ratio
    h = 0.5 * intent.max_thickness_ratio
    fore_x = max(0.03, min(xt * 0.55, xt - 0.02))
    aft_x = min(0.97, max(xt + 0.02, xt + 0.45 * (1.0 - xt)))
    anchors_x = np.array([0.0, fore_x, xt, aft_x, 1.0])
    anchors_y = np.array(
        [
            0.0,
            h * (0.65 + 0.08 * intent.fore_shape_factor),
            h,
            h * (0.55 + 0.08 * intent.aft_shape_factor),
            0.0,
        ]
    )
    return PchipInterpolator(anchors_x, anchors_y)(u)


def _resolve_thickness(family: CurveFamily, target_u: np.ndarray, target: np.ndarray):
    if len(target) != len(target_u):
        target = np.interp(target_u, np.linspace(0.0, 1.0, len(target)), target)
    if family == CurveFamily.BEZIER:
        degree = 6
        basis_spec = BezierCurveSpec(degree=degree, control_points=[[0.0]] * (degree + 1))
        matrix = np.column_stack(
            [
                CurveEvaluator(
                    basis_spec.model_copy(
                        update={
                            "control_points": [
                                [1.0 if row == column else 0.0] for row in range(degree + 1)
                            ]
                        }
                    )
                ).evaluate(target_u)[:, 0]
                for column in range(degree + 1)
            ]
        )
        result = lsq_linear(matrix, target, bounds=(0.0, 0.2), tol=1e-14)
        values = result.x
        values[[0, -1]] = 0.0
        curve = BezierCurveSpec(degree=degree, control_points=[[float(value)] for value in values])
    else:
        knots = open_uniform_knots(7, 3)
        weights = (
            np.array([1.0, 0.86, 1.04, 1.18, 1.05, 0.9, 1.0])
            if family == CurveFamily.NURBS
            else None
        )
        columns = []
        for column in range(7):
            controls = [[1.0 if row == column else 0.0] for row in range(7)]
            if weights is None:
                basis_curve = BSplineCurveSpec(degree=3, control_points=controls, knots=knots)
            else:
                basis_curve = NURBSCurveSpec(
                    degree=3,
                    control_points=controls,
                    knots=knots,
                    weights=weights.tolist(),
                )
            columns.append(CurveEvaluator(basis_curve).evaluate(target_u)[:, 0])
        matrix = np.column_stack(columns)
        result = lsq_linear(matrix, target, bounds=(0.0, 0.2), tol=1e-14)
        values = result.x
        values[[0, -1]] = 0.0
        if weights is None:
            curve = BSplineCurveSpec(
                degree=3,
                control_points=[[float(value)] for value in values],
                knots=knots,
            )
        else:
            curve = NURBSCurveSpec(
                degree=3,
                control_points=[[float(value)] for value in values],
                knots=knots,
                weights=weights.tolist(),
            )
    predicted = CurveEvaluator(curve).evaluate(target_u)[:, 0]
    return curve, float(np.sqrt(np.mean((predicted - target) ** 2)))


def _side_point_and_tangent(
    camber: CurveEvaluator,
    thickness: CurveEvaluator,
    parameter: float,
    suction: bool,
) -> tuple[np.ndarray, np.ndarray]:
    u = np.array([parameter])
    point = camber.evaluate(u)[0]
    first = camber.evaluate(u, 1)[0]
    second = camber.evaluate(u, 2)[0]
    speed = float(np.linalg.norm(first))
    tangent = first / speed
    normal = np.array([-tangent[1], tangent[0]])
    curvature = (first[0] * second[1] - first[1] * second[0]) / speed**3
    half = float(thickness.evaluate(u)[0, 0])
    half_derivative = float(thickness.evaluate(u, 1)[0, 0])
    side_sign = 1.0 if suction else -1.0
    side_point = point + side_sign * half * normal
    side_derivative = (
        first * (1.0 - side_sign * half * curvature) + side_sign * half_derivative * normal
    )
    return side_point, side_derivative / np.linalg.norm(side_derivative)


def _solve_edge_circle(
    camber: CurveEvaluator,
    suction_thickness: CurveEvaluator,
    pressure_thickness: CurveEvaluator,
    radius: float,
    *,
    leading: bool,
) -> _EdgeSolution:
    bounds = ([1e-5, 1e-5], [0.38, 0.38]) if leading else ([0.62, 0.62], [0.99999, 0.99999])
    initial = [max(0.01, 4 * radius)] * 2 if leading else [min(0.99, 1 - 4 * radius)] * 2

    def centers(values):
        suction_point, suction_tangent = _side_point_and_tangent(
            camber, suction_thickness, values[0], True
        )
        pressure_point, pressure_tangent = _side_point_and_tangent(
            camber, pressure_thickness, values[1], False
        )
        suction_inward = np.array([suction_tangent[1], -suction_tangent[0]])
        pressure_inward = np.array([-pressure_tangent[1], pressure_tangent[0]])
        return (
            suction_point + radius * suction_inward,
            pressure_point + radius * pressure_inward,
            suction_point,
            pressure_point,
            suction_tangent,
            pressure_tangent,
        )

    result = least_squares(
        lambda values: centers(values)[0] - centers(values)[1],
        initial,
        bounds=bounds,
        xtol=1e-13,
        ftol=1e-13,
        gtol=1e-13,
        max_nfev=200,
    )
    cs, cp, ps, pp, ts, tp = centers(result.x)
    center = 0.5 * (cs + cp)
    residual = float(np.linalg.norm(cs - cp))
    if residual > 2e-5:
        raise ValueError(f"{'前缘' if leading else '尾缘'}圆无法与双侧同时相切")
    start_point, end_point = (pp, ps) if leading else (ps, pp)
    arc = _outer_arc(center, start_point, end_point, leading=leading)
    nose_index = int(np.argmin(arc[:, 0])) if leading else int(np.argmax(arc[:, 0]))
    start_radial = arc[0] - center
    end_radial = arc[-1] - center
    start_tangent = np.array([-start_radial[1], start_radial[0]])
    end_tangent = np.array([-end_radial[1], end_radial[0]])
    contact_tangents = (tp, ts) if leading else (ts, tp)
    tangency = max(
        angle_between_lines(start_tangent, contact_tangents[0]),
        angle_between_lines(end_tangent, contact_tangents[1]),
    )
    return _EdgeSolution(
        center=center,
        suction_u=float(result.x[0]),
        pressure_u=float(result.x[1]),
        suction_point=ps,
        pressure_point=pp,
        arc=arc,
        nose_index=nose_index,
        center_residual=residual,
        tangency_error_deg=tangency,
    )


def _outer_arc(
    center: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    *,
    leading: bool,
    count: int = 121,
) -> np.ndarray:
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    radius = 0.5 * (np.linalg.norm(start - center) + np.linalg.norm(end - center))
    candidates = []
    for direction in (1.0, -1.0):
        delta = (end_angle - start_angle) % (2 * math.pi)
        if direction < 0:
            delta = -((start_angle - end_angle) % (2 * math.pi))
        angles = np.linspace(start_angle, start_angle + delta, count)
        points = center + radius * np.column_stack([np.cos(angles), np.sin(angles)])
        candidates.append(points)

    def score(points: np.ndarray) -> float:
        return float(np.min(points[:, 0])) if leading else -float(np.max(points[:, 0]))

    return min(candidates, key=score)


def _resample_polyline(points: np.ndarray, count: int) -> np.ndarray:
    delta = np.diff(points, axis=0)
    length = np.linalg.norm(delta, axis=1)
    keep = np.concatenate([[True], length > 1e-13])
    points = points[keep]
    distance = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))])
    beta = np.linspace(0.0, math.pi, count)
    target = 0.5 * (1.0 - np.cos(beta)) * distance[-1]
    return np.column_stack([np.interp(target, distance, points[:, axis]) for axis in range(2)])


def _normal_offset_error(
    camber: np.ndarray,
    normal: np.ndarray,
    suction: np.ndarray,
    pressure: np.ndarray,
    suction_half: np.ndarray,
    pressure_half: np.ndarray,
) -> float:
    suction_error = suction - camber - suction_half[:, None] * normal
    pressure_error = pressure - camber + pressure_half[:, None] * normal
    return float(
        max(
            np.max(np.linalg.norm(suction_error, axis=1)),
            np.max(np.linalg.norm(pressure_error, axis=1)),
        )
    )
