from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import BSpline, PchipInterpolator
from scipy.optimize import lsq_linear

from .curves import (
    CurveEvaluator,
    angle_between_lines,
    open_uniform_knots,
    signed_curvature,
    unit_tangent_and_normal,
)
from .models import Handedness
from .models_v2 import (
    AirfoilGeometryV2,
    BSplineCurveSpec,
    EdgeSpec,
    GeometrySpecV2,
)


@dataclass(frozen=True)
class SurfaceFit:
    curve: BSplineCurveSpec
    rms_error: float
    max_error: float
    iterations: int


def resolve_geometry_spec(spec: GeometrySpecV2) -> GeometrySpecV2:
    """把基础工程意图确定性解析为统一 B-spline 显式合同。"""

    if spec.resolved:
        _check_camber_endpoints(spec.camber_curve)
        return spec

    intent = spec.intent
    sign = 1.0 if intent.handedness == Handedness.RIGHT else -1.0
    inlet_local = math.radians(sign * (intent.inlet_metal_angle_deg - intent.stagger_deg))
    outlet_local = math.radians(sign * (intent.outlet_metal_angle_deg - intent.stagger_deg))
    camber = BSplineCurveSpec(
        control_points=[
            [0.0, 0.0],
            [
                intent.camber_le_tangent_ratio * math.cos(inlet_local),
                intent.camber_le_tangent_ratio * math.sin(inlet_local),
            ],
            [
                1.0 - intent.camber_te_tangent_ratio * math.cos(outlet_local),
                -intent.camber_te_tangent_ratio * math.sin(outlet_local),
            ],
            [1.0, 0.0],
        ],
        knots=open_uniform_knots(4),
    )
    edge = EdgeSpec(
        leading_edge_radius_ratio=intent.leading_edge_radius_ratio,
        trailing_edge_radius_ratio=intent.trailing_edge_radius_ratio,
    )

    design_xt = intent.max_thickness_x_ratio
    amplitude_scale = 1.0
    resolved: GeometrySpecV2 | None = None
    realized_tmax = math.nan
    realized_xt = math.nan
    for _ in range(8):
        thickness, thickness_rms = _basic_thickness_curve(
            spec,
            design_xt=design_xt,
            amplitude_scale=amplitude_scale,
        )
        resolved = spec.model_copy(
            update={
                "camber_curve": camber,
                "suction_thickness_curve": thickness,
                "pressure_thickness_curve": thickness,
                "edge": edge,
            }
        )
        suction_fit = _fit_surface(resolved, suction=True)
        pressure_fit = _fit_surface(resolved, suction=False)
        realized_tmax, realized_xt = _realized_thickness_contract(
            camber,
            suction_fit.curve,
            pressure_fit.curve,
        )
        t_error = intent.max_thickness_ratio - realized_tmax
        x_error = intent.max_thickness_x_ratio - realized_xt
        if abs(t_error) <= 2e-5 and abs(x_error) <= 5e-4:
            break
        if realized_tmax <= 1e-8:
            raise ValueError("基础厚度解析退化")
        amplitude_scale *= intent.max_thickness_ratio / realized_tmax
        design_xt = float(np.clip(design_xt + x_error, 0.12, 0.78))

    assert resolved is not None
    return resolved.model_copy(
        update={
            "resolution_residuals": {
                "half_thickness_rms": thickness_rms,
                "resolved_tmax_error": realized_tmax - intent.max_thickness_ratio,
                "resolved_xt_error": realized_xt - intent.max_thickness_x_ratio,
            }
        }
    )


def generate_profile_v2(spec: GeometrySpecV2) -> AirfoilGeometryV2:
    resolved = resolve_geometry_spec(spec)
    assert resolved.camber_curve is not None
    assert resolved.suction_thickness_curve is not None
    assert resolved.pressure_thickness_curve is not None
    assert resolved.edge is not None

    camber_eval = CurveEvaluator(resolved.camber_curve)
    suction_fit = _fit_surface(resolved, suction=True)
    pressure_fit = _fit_surface(resolved, suction=False)
    suction_eval = CurveEvaluator(suction_fit.curve)
    pressure_eval = CurveEvaluator(pressure_fit.curve)

    beta = np.linspace(0.0, math.pi, resolved.points_per_side)
    sample_u = 0.5 * (1.0 - np.cos(beta))
    suction = suction_eval.evaluate(sample_u)
    pressure = pressure_eval.evaluate(sample_u)
    camber = camber_eval.evaluate(sample_u)
    _, normal = unit_tangent_and_normal(camber_eval, sample_u)
    thickness = np.linalg.norm(suction - pressure, axis=1)

    le = np.array([0.0, 0.0])
    te = np.array([1.0, 0.0])
    endpoints = np.asarray([le, te])
    if not np.allclose(suction[[0, -1]], endpoints, atol=2e-13, rtol=0.0):
        raise ValueError("吸力面未在统一鼻点闭合")
    if not np.allclose(pressure[[0, -1]], endpoints, atol=2e-13, rtol=0.0):
        raise ValueError("压力面未在统一鼻点闭合")

    tangent, _ = unit_tangent_and_normal(camber_eval, np.array([0.0, 1.0]))
    le_center = le + resolved.edge.leading_edge_radius_ratio * tangent[0]
    te_center = te - resolved.edge.trailing_edge_radius_ratio * tangent[1]
    le_circle = _circle(le_center, resolved.edge.leading_edge_radius_ratio)
    te_circle = _circle(te_center, resolved.edge.trailing_edge_radius_ratio)

    edge_metrics = _edge_metrics(
        resolved,
        suction_fit,
        pressure_fit,
        tangent,
        le_center,
        te_center,
    )
    return AirfoilGeometryV2(
        geometry_id=resolved.geometry_id,
        intent_id=resolved.intent.intent_id,
        spec=resolved,
        suction=suction.tolist(),
        pressure=pressure.tolist(),
        camber=camber.tolist(),
        thickness=thickness.tolist(),
        normals=normal.tolist(),
        suction_surface_curve=suction_fit.curve,
        pressure_surface_curve=pressure_fit.curve,
        leading_osculating_circle=le_circle.tolist(),
        trailing_osculating_circle=te_circle.tolist(),
        realized_stagger_deg=(
            resolved.intent.stagger_deg
            if resolved.intent.handedness == Handedness.RIGHT
            else -resolved.intent.stagger_deg
        ),
        edge_metrics=edge_metrics,
        metadata={
            "mathematical_kernel": "unified-bspline-g2-constrained-normal-fit",
            "leading_edge_center": le_center.tolist(),
            "trailing_edge_center": te_center.tolist(),
            "leading_edge_nose": le.tolist(),
            "trailing_edge_nose": te.tolist(),
            "leading_edge_tangent": tangent[0].tolist(),
            "trailing_edge_tangent": tangent[1].tolist(),
            "surface_fit_interval": [resolved.surface_fit_start, resolved.surface_fit_end],
            "surface_fit_iterations": max(suction_fit.iterations, pressure_fit.iterations),
        },
    )


def reference_stagger(spec: GeometrySpecV2) -> float:
    sign = 1.0 if spec.intent.handedness == Handedness.RIGHT else -1.0
    return sign * spec.intent.stagger_deg


def _basic_thickness_curve(
    spec: GeometrySpecV2,
    *,
    design_xt: float,
    amplitude_scale: float,
) -> tuple[BSplineCurveSpec, float]:
    intent = spec.intent
    knots = open_uniform_knots(8)
    parameter = np.linspace(0.0, 1.0, 401)
    half = 0.5 * intent.max_thickness_ratio * amplitude_scale
    fore_x = max(0.03, min(design_xt * 0.55, design_xt - 0.02))
    aft_x = min(0.97, max(design_xt + 0.02, design_xt + 0.45 * (1.0 - design_xt)))
    anchors_x = np.array([0.0, fore_x, design_xt, aft_x, 1.0])
    anchors_y = np.array(
        [
            intent.leading_edge_radius_ratio,
            half * (0.65 + 0.08 * intent.fore_shape_factor),
            half,
            half * (0.55 + 0.08 * intent.aft_shape_factor),
            intent.trailing_edge_radius_ratio,
        ]
    )
    target = PchipInterpolator(anchors_x, anchors_y)(parameter)
    basis = BSpline(knots, np.eye(8), 3, extrapolate=False)(parameter)
    fixed = (
        basis[:, 0] * intent.leading_edge_radius_ratio
        + basis[:, -1] * intent.trailing_edge_radius_ratio
    )
    result = lsq_linear(
        basis[:, 1:-1],
        target - fixed,
        bounds=(1e-6, 0.2),
        tol=1e-13,
        lsmr_tol=1e-13,
    )
    values = np.concatenate(
        [
            [intent.leading_edge_radius_ratio],
            result.x,
            [intent.trailing_edge_radius_ratio],
        ]
    )
    curve = BSplineCurveSpec(
        control_points=[[float(value)] for value in values],
        knots=knots,
    )
    predicted = CurveEvaluator(curve).evaluate(parameter)[:, 0]
    rms = float(np.sqrt(np.mean((predicted - target) ** 2)))
    return curve, rms


def _fit_surface(spec: GeometrySpecV2, *, suction: bool) -> SurfaceFit:
    assert spec.camber_curve is not None
    assert spec.suction_thickness_curve is not None
    assert spec.pressure_thickness_curve is not None
    assert spec.edge is not None
    camber = CurveEvaluator(spec.camber_curve)
    thickness_spec = (
        spec.suction_thickness_curve if suction else spec.pressure_thickness_curve
    )
    thickness = CurveEvaluator(thickness_spec)
    side_sign = 1.0 if suction else -1.0

    # 增加内部自由度，把端部 G2 约束的影响限制在鼻点邻域；控制点数量
    # 不改变工程变量数量，仍由双侧 8 值厚度规律驱动。
    base_u = np.linspace(0.0, 1.0, 18)
    base_camber = camber.evaluate(base_u)
    _, base_normal = unit_tangent_and_normal(camber, base_u)
    base_half = thickness.evaluate(base_u)[:, 0]
    base = base_camber + side_sign * base_half[:, None] * base_normal
    base[0] = base_camber[0]
    base[-1] = base_camber[-1]

    knots = open_uniform_knots(20)
    controls = np.zeros((20, 2), dtype=float)
    controls[0] = base_camber[0]
    controls[-1] = base_camber[-1]
    controls[2:-2] = base[1:-1]

    fit_u = np.linspace(spec.surface_fit_start, spec.surface_fit_end, spec.surface_fit_samples)
    fit_camber = camber.evaluate(fit_u)
    _, fit_normal = unit_tangent_and_normal(camber, fit_u)
    fit_half = thickness.evaluate(fit_u)[:, 0]
    target = fit_camber + side_sign * fit_half[:, None] * fit_normal
    basis = BSpline(knots, np.eye(20), 3, extrapolate=False)(fit_u)
    unknown = np.arange(2, 18)
    regularization = math.sqrt(spec.surface_fit_regularization)
    matrix = np.vstack([basis[:, unknown], regularization * np.eye(len(unknown))])
    previous = controls.copy()
    iterations = 0
    for iteration_index in range(1, 13):
        iterations = iteration_index
        _apply_edge_controls(controls, camber, spec.edge, side_sign, knots)
        known = np.array([0, 1, 18, 19])
        rhs = target - basis[:, known] @ controls[known]
        augmented = np.vstack([rhs, regularization * base[1:-1]])
        controls[unknown] = np.linalg.lstsq(matrix, augmented, rcond=None)[0]
        change = float(np.max(np.abs(controls - previous)))
        previous = controls.copy()
        if change <= 1e-12:
            break
    _apply_edge_controls(controls, camber, spec.edge, side_sign, knots)
    curve = BSplineCurveSpec(control_points=controls.tolist(), knots=knots)
    deviation = np.linalg.norm(CurveEvaluator(curve).evaluate(fit_u) - target, axis=1)
    return SurfaceFit(
        curve=curve,
        rms_error=float(np.sqrt(np.mean(deviation**2))),
        max_error=float(np.max(deviation)),
        iterations=iterations,
    )


def _apply_edge_controls(
    controls: np.ndarray,
    camber: CurveEvaluator,
    edge: EdgeSpec,
    side_sign: float,
    knots: list[float],
) -> None:
    tangent, normal = unit_tangent_and_normal(camber, np.array([0.0, 1.0]))
    controls[1] = _g2_control_point(
        endpoint=controls[0],
        adjacent=controls[2],
        tangent=tangent[0],
        outward_normal=side_sign * normal[0],
        radius=edge.leading_edge_radius_ratio,
        knots=knots,
        start=True,
    )
    controls[-2] = _g2_control_point(
        endpoint=controls[-1],
        adjacent=controls[-3],
        tangent=tangent[1],
        outward_normal=side_sign * normal[1],
        radius=edge.trailing_edge_radius_ratio,
        knots=knots,
        start=False,
    )


def _g2_control_point(
    *,
    endpoint: np.ndarray,
    adjacent: np.ndarray,
    tangent: np.ndarray,
    outward_normal: np.ndarray,
    radius: float,
    knots: list[float],
    start: bool,
) -> np.ndarray:
    p = 3
    if start:
        a1 = p / knots[p + 1]
        b2 = p * (p - 1) / knots[p + 1] / knots[p + 2]
    else:
        m = len(knots) - 1
        n = m - p - 1
        a1 = -p / (1.0 - knots[n])
        b2 = p * (p - 1) / (1.0 - knots[n]) / (1.0 - knots[n - 1])
    tangent_projection = abs(float(np.dot(adjacent - endpoint, tangent)))
    if tangent_projection <= 1e-10:
        raise ValueError("端部相邻控制点与中弧线切向投影退化")
    distance = math.sqrt((b2 / a1**2) * radius * tangent_projection)
    return endpoint + outward_normal * distance


def _realized_thickness_contract(
    camber: BSplineCurveSpec,
    suction: BSplineCurveSpec,
    pressure: BSplineCurveSpec,
) -> tuple[float, float]:
    parameter = np.linspace(0.0, 1.0, 1601)
    suction_points = CurveEvaluator(suction).evaluate(parameter)
    pressure_points = CurveEvaluator(pressure).evaluate(parameter)
    values = np.linalg.norm(suction_points - pressure_points, axis=1)
    peak = int(np.argmax(values))
    x = CurveEvaluator(camber).evaluate(parameter)[peak, 0]
    return float(values[peak]), float(x)


def _edge_metrics(
    spec: GeometrySpecV2,
    suction_fit: SurfaceFit,
    pressure_fit: SurfaceFit,
    camber_tangent: np.ndarray,
    le_center: np.ndarray,
    te_center: np.ndarray,
) -> dict[str, float]:
    assert spec.edge is not None
    suction = CurveEvaluator(suction_fit.curve)
    pressure = CurveEvaluator(pressure_fit.curve)
    parameters = np.array([0.0, 1.0])
    suction_curvature = signed_curvature(suction, parameters)
    pressure_curvature = signed_curvature(pressure, parameters)
    suction_derivative = suction.evaluate(parameters, 1)
    pressure_derivative = pressure.evaluate(parameters, 1)

    target = np.array(
        [spec.edge.leading_edge_radius_ratio, spec.edge.trailing_edge_radius_ratio]
    )
    actual_suction = 1.0 / np.maximum(np.abs(suction_curvature), 1e-15)
    actual_pressure = 1.0 / np.maximum(np.abs(pressure_curvature), 1e-15)

    def center(point, derivative, curvature):
        unit = derivative / np.linalg.norm(derivative)
        left_normal = np.array([-unit[1], unit[0]])
        return point + left_normal / curvature

    expected_centers = (le_center, te_center)
    center_errors = []
    for index, expected in enumerate(expected_centers):
        point = np.array([float(index), 0.0])
        center_errors.extend(
            [
                np.linalg.norm(
                    center(point, suction_derivative[index], suction_curvature[index]) - expected
                ),
                np.linalg.norm(
                    center(point, pressure_derivative[index], pressure_curvature[index]) - expected
                ),
            ]
        )
    return {
        "leading_edge_radius_ratio": float(0.5 * (actual_suction[0] + actual_pressure[0])),
        "trailing_edge_radius_ratio": float(0.5 * (actual_suction[1] + actual_pressure[1])),
        "leading_suction_radius_ratio": float(actual_suction[0]),
        "leading_pressure_radius_ratio": float(actual_pressure[0]),
        "trailing_suction_radius_ratio": float(actual_suction[1]),
        "trailing_pressure_radius_ratio": float(actual_pressure[1]),
        "leading_radius_relative_error": float(
            max(abs(actual_suction[0] - target[0]), abs(actual_pressure[0] - target[0]))
            / target[0]
        ),
        "trailing_radius_relative_error": float(
            max(abs(actual_suction[1] - target[1]), abs(actual_pressure[1] - target[1]))
            / target[1]
        ),
        "leading_tangency_error_deg": angle_between_lines(
            suction_derivative[0], pressure_derivative[0]
        ),
        "trailing_tangency_error_deg": angle_between_lines(
            suction_derivative[1], pressure_derivative[1]
        ),
        "leading_camber_normal_error_deg": max(
            angle_between_lines(
                suction_derivative[0], [-camber_tangent[0, 1], camber_tangent[0, 0]]
            ),
            angle_between_lines(
                pressure_derivative[0], [-camber_tangent[0, 1], camber_tangent[0, 0]]
            ),
        ),
        "trailing_camber_normal_error_deg": max(
            angle_between_lines(
                suction_derivative[1], [-camber_tangent[1, 1], camber_tangent[1, 0]]
            ),
            angle_between_lines(
                pressure_derivative[1], [-camber_tangent[1, 1], camber_tangent[1, 0]]
            ),
        ),
        "osculating_center_error": float(max(center_errors)),
        "normal_fit_rms": float(
            math.sqrt(0.5 * (suction_fit.rms_error**2 + pressure_fit.rms_error**2))
        ),
        "normal_fit_max": float(max(suction_fit.max_error, pressure_fit.max_error)),
    }


def _circle(center: np.ndarray, radius: float, count: int = 121) -> np.ndarray:
    angle = np.linspace(0.0, 2.0 * math.pi, count)
    return center + radius * np.column_stack([np.cos(angle), np.sin(angle)])


def _check_camber_endpoints(curve: BSplineCurveSpec | None) -> None:
    assert curve is not None
    endpoints = CurveEvaluator(curve).evaluate([0.0, 1.0])
    if not np.allclose(endpoints, [[0.0, 0.0], [1.0, 0.0]], atol=1e-12):
        raise ValueError("显式中弧线端点必须为 (0,0) 与 (1,0)")
