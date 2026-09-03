"""Linked, exact normal offsets. No independent surface refit is permitted here."""

from __future__ import annotations

import numpy as np
from scipy.interpolate import BSpline, CubicHermiteSpline, PchipInterpolator
from scipy.optimize import lsq_linear

from .curves import CurveEvaluator, unit_tangent_and_normal
from .models_naca65 import NACA65FitGeometry, NACA65Geometry, NACA65SplineSpec
from .models_v2 import BSplineCurveSpec


def fit_decomposition(reference: NACA65Geometry, max_iterations: int = 80):
    u = np.asarray(reference.construction_parameter)
    c = np.asarray(reference.construction_camber).copy()
    c[[0, -1]] = [[0.0, 0.0], [1.0, 0.0]]
    n = np.asarray(reference.construction_normals)
    length = np.linalg.norm(np.diff(c, axis=0), axis=1).sum()
    derivatives = length * np.column_stack([n[:, 1], -n[:, 0]])
    # Cluster knots at the nose and at the NASA 6A straight-tail transition.
    x_breaks = np.array(
        [
            0.00025,
            0.001,
            0.0025,
            0.005,
            0.01,
            0.02,
            0.04,
            0.08,
            0.15,
            0.25,
            0.35,
            0.45,
            0.55,
            0.65,
            0.75,
            0.8,
            0.82,
            0.85,
            0.9,
            0.95,
            0.98,
            0.995,
            0.999,
        ]
    )
    knots = np.r_[np.zeros(4), np.interp(x_breaks, c[:, 0], u), np.ones(4)]
    count = len(knots) - 4
    sample = 0.5 * (1 - np.cos(np.linspace(0, np.pi, 1601)))
    basis_spline = BSpline(knots, np.eye(count), 3)
    basis = basis_spline(sample)
    target_curve = CubicHermiteSpline(u, c, derivatives)
    target = target_curve(sample)
    controls = np.zeros((count, 2))
    controls[0], controls[-1] = c[0], c[-1]
    controls[1] = c[0] + derivatives[0] * knots[4] / 3
    controls[-2] = c[-1] - derivatives[-1] * (1 - knots[-5]) / 3
    known = [0, 1, count - 2, count - 1]
    free = np.arange(2, count - 2)
    # Position is primary; a small derivative term stabilizes the normal field.
    derivative_weight = 2e-4
    full_matrix = np.vstack([basis, derivative_weight * basis_spline(sample, 1)])
    full_target = np.vstack([target, derivative_weight * target_curve(sample, 1)])
    controls[free] = np.linalg.lstsq(
        full_matrix[:, free], full_target - full_matrix[:, known] @ controls[known], rcond=None
    )[0]
    camber = BSplineCurveSpec(control_points=controls.tolist(), knots=knots.tolist())

    half = np.asarray(reference.construction_half_thickness)
    interior = (u > 0) & (u < 1)
    q = half[interior] / (np.sqrt(u[interior]) * (1 - u[interior]))
    # LE square-root and sharp-TE linear factors enforce closure without a
    # nonphysical radius-as-thickness endpoint or a second surface spline.
    shape_target = PchipInterpolator(u[interior], q, extrapolate=True)(sample)
    shape_fit = lsq_linear(basis, shape_target, bounds=(1e-8, 2.0), max_iter=max_iterations)
    spec = NACA65SplineSpec(
        camber_curve=camber,
        thickness_shape_curve=BSplineCurveSpec(
            control_points=shape_fit.x[:, None].tolist(), knots=knots.tolist()
        ),
    )
    return spec, shape_fit


def evaluate_normal_offset(spec: NACA65SplineSpec, parameter: np.ndarray):
    u = np.asarray(parameter, dtype=float)
    if np.any((u < 0) | (u > 1)):
        raise ValueError("法向叠加参数必须位于 [0, 1]")
    ce = CurveEvaluator(spec.camber_curve)
    camber = ce.evaluate(u)
    _, normals = unit_tangent_and_normal(ce, u)
    shape = CurveEvaluator(spec.thickness_shape_curve).evaluate(u)[:, 0]
    half = np.sqrt(u) * (1 - u) * shape
    suction = camber + half[:, None] * normals
    pressure = camber - half[:, None] * normals
    return suction, pressure, camber, 2 * half, normals


def generate_normal_offset_profile(
    spec: NACA65SplineSpec, source_id: str = "", count: int = 201
) -> NACA65FitGeometry:
    u = 0.5 * (1 - np.cos(np.linspace(0, np.pi, count)))
    suction, pressure, camber, thickness, normals = evaluate_normal_offset(spec, u)
    ce = CurveEvaluator(spec.camber_curve)
    first = ce.evaluate(np.array([0.0]), 1)[0]
    tangent = first / np.linalg.norm(first)
    q0 = float(CurveEvaluator(spec.thickness_shape_curve).evaluate([0.0])[0, 0])
    # Use v=sqrt(s): P'(0)=q0*n, P''(0)=2*C'(0), hence r=q0²/(2|C'|).
    radius = q0 * q0 / (2 * np.linalg.norm(first))
    center = camber[0] + radius * tangent
    angles = np.linspace(0, 2 * np.pi, 121)
    circle = center + radius * np.column_stack([np.cos(angles), np.sin(angles)])
    midpoint_error = np.linalg.norm(0.5 * (suction + pressure) - camber, axis=1)
    tangents = np.column_stack([normals[:, 1], -normals[:, 0]])
    orthogonal_error = np.abs(np.sum((suction - camber) * tangents, axis=1))
    return NACA65FitGeometry(
        geometry_id=spec.geometry_id,
        source_id=source_id,
        spec=spec,
        suction=suction.tolist(),
        pressure=pressure.tolist(),
        camber=camber.tolist(),
        thickness=thickness.tolist(),
        normals=normals.tolist(),
        parameter=u.tolist(),
        leading_osculating_circle=circle.tolist(),
        edge_metrics={
            "leading_edge_radius_ratio": float(radius),
            "trailing_edge_radius_ratio": 0.0,
            "midpoint_error_max": float(midpoint_error.max()),
            "normal_offset_error_max": float(orthogonal_error.max()),
            "closure_error_max": float(
                np.linalg.norm(suction[[0, -1]] - pressure[[0, -1]], axis=1).max()
            ),
        },
        metadata={
            "mathematical_kernel": spec.generator_version,
            "leading_edge_center": center.tolist(),
            "leading_edge_nose": camber[0].tolist(),
            "trailing_edge_nose": camber[-1].tolist(),
            "trailing_edge_kind": "sharp; no common osculating circle",
            "surface_authority": "C(s) +/- sqrt(s)*(1-s)*B(s)*n(s); no surface refit",
        },
    )
