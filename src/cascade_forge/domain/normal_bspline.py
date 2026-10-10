"""Linked, exact normal offsets. No independent surface refit is permitted here."""

from __future__ import annotations

import numpy as np
from scipy.interpolate import BSpline, PchipInterpolator
from scipy.optimize import lsq_linear

from cascade_forge.domain.curves import CurveEvaluator, unit_tangent_and_normal
from cascade_forge.domain.models import BSplineCurveSpec
from cascade_forge.domain.models_naca65 import (
    NACA65CamberCurveSpec,
    NACA65FitGeometry,
    NACA65Geometry,
    NACA65SplineSpec,
)


def fit_decomposition(reference: NACA65Geometry, max_iterations: int = 80):
    compressor = reference.spec.family.value == "65-compressor"
    u = np.asarray(reference.construction_parameter)
    c = np.asarray(reference.construction_camber).copy()
    c[[0, -1]] = [[0.0, 0.0], [1.0, 0.0]]
    n = np.asarray(reference.construction_normals)
    half = np.asarray(reference.construction_half_thickness).copy()
    source_decomposition = "sixseries-mean-line-normal-stations"
    if compressor:
        source_decomposition = "TR1368-blower-table-and-a1-mean-line-before-engineering-rounding"
    if reference.spec.family.value == "65A":
        # Sixseries replaces the two surfaces independently by straight lines
        # aft of x~=0.82.  The pre-replacement analytical mean line/thickness
        # are therefore not the geometry that must be fitted in this region.
        source_suction = np.asarray(reference.raw_suction)
        source_pressure = np.asarray(reference.raw_pressure)
        c = 0.5 * (source_suction + source_pressure)
        half = 0.5 * np.linalg.norm(source_suction - source_pressure, axis=1)
        c[[0, -1]] = [[0.0, 0.0], [1.0, 0.0]]
        u = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(c, axis=0), axis=1))]
        u /= u[-1]
        source_decomposition = "paired-native-surfaces-after-65A-tail-rule"
    half[0] = 0.0
    if not compressor:
        half[-1] = 0.0
    length = np.linalg.norm(np.diff(c, axis=0), axis=1).sum()
    derivatives = length * np.column_stack([n[:, 1], -n[:, 0]])
    # The analytical a=1 mean line has a logarithmic endpoint derivative.
    # A finite cubic spline cannot represent that singularity; use the
    # one-sided derivative of the actual resolved station geometry instead of
    # probing at an arbitrary 1-epsilon and creating a trailing-edge hook.
    target_curve = PchipInterpolator(u, c)
    derivatives[-1] = target_curve.derivative()(1.0)
    # Resolve both edges and the NASA 6A straight-tail transition without the
    # extremely close knot sequence that caused endpoint curvature ringing.
    x_breaks = np.array(
        [
            0.001,
            0.005,
            0.015,
            0.04,
            0.08,
            0.15,
            0.25,
            0.4,
            0.55,
            0.7,
            0.82,
            0.9,
            0.96,
            0.99,
        ]
    )
    knots = np.r_[np.zeros(6), x_breaks, np.ones(6)]
    count = len(knots) - 6
    sample = 0.5 * (1 - np.cos(np.linspace(0, np.pi, 1601)))
    # A cubic mean line is only C2, so its variable normal offset need not
    # have continuous curvature. Quintic/simple knots give C4 mean line and
    # C3 normal, while cubic thickness still makes the final sides C2.
    basis_spline = BSpline(knots, np.eye(count), 5)
    basis = basis_spline(sample)
    target = target_curve(sample)
    controls = np.zeros((count, 2))
    controls[0], controls[-1] = c[0], c[-1]
    controls[1] = c[0] + derivatives[0] * knots[6] / 5
    controls[-2] = c[-1] - derivatives[-1] * (1 - knots[-7]) / 5
    known = [0, 1, count - 2, count - 1]
    free = np.arange(2, count - 2)
    # Minimise mean squared position error plus integrated squared second derivative.
    # The fairness term removes nonphysical endpoint ringing while retaining
    # the NACA loading break and the 65A tail transition.
    grid = np.linspace(0.0, 1.0, 1001)
    camber_fairness = 2e-8
    position_matrix = basis / np.sqrt(len(sample))
    position_target = target / np.sqrt(len(sample))
    fair_matrix = basis_spline(grid, 2) * np.sqrt(camber_fairness / (len(grid) - 1))
    full_matrix = np.vstack([position_matrix, fair_matrix])
    full_target = np.vstack([position_target, np.zeros((len(grid), 2))])
    controls[free] = np.linalg.lstsq(
        full_matrix[:, free], full_target - full_matrix[:, known] @ controls[known], rcond=None
    )[0]
    camber = NACA65CamberCurveSpec(control_points=controls.tolist(), knots=knots.tolist())

    interior = (u > 0) & (u < 1)
    factor = np.sqrt(u[interior])
    if not compressor:
        factor *= 1 - u[interior]
    q = half[interior] / factor
    # LE square-root and sharp-TE linear factors enforce closure without a
    # nonphysical radius-as-thickness endpoint or a second surface spline.
    shape_target = PchipInterpolator(u[interior], q, extrapolate=True)(sample)
    shape_target[0] = q[0]
    shape_target[-1] = (
        half[-1] if compressor else max(1e-8, -float(PchipInterpolator(u, half).derivative()(1.0)))
    )
    thickness_fairness = 1e-8
    thickness_knots = np.r_[np.zeros(4), x_breaks, np.ones(4)]
    thickness_basis = BSpline(thickness_knots, np.eye(len(thickness_knots) - 4), 3)
    shape_fit = lsq_linear(
        np.vstack(
            [
                thickness_basis(sample) / np.sqrt(len(sample)),
                thickness_basis(grid, 2) * np.sqrt(thickness_fairness / (len(grid) - 1)),
            ]
        ),
        np.r_[shape_target / np.sqrt(len(sample)), np.zeros(len(grid))],
        bounds=(1e-8, 2.0),
        max_iter=max_iterations,
    )
    spec = NACA65SplineSpec(
        generator_version="naca65-compressor-bspline-v1"
        if compressor
        else "naca65-normal-bspline-v3",
        thickness_law="sqrt(s)*B(s)" if compressor else "sqrt(s)*(1-s)*B(s)",
        trailing_edge="rounded" if compressor else "sharp",
        trailing_edge_radius_ratio=reference.metadata["trailing_edge_radius_ratio"]
        if compressor
        else 0,
        camber_curve=camber,
        thickness_shape_curve=BSplineCurveSpec(
            control_points=shape_fit.x[:, None].tolist(), knots=thickness_knots.tolist()
        ),
        source_decomposition=source_decomposition,
        camber_fairness_weight=camber_fairness,
        thickness_fairness_weight=thickness_fairness,
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
    half = np.sqrt(u) * shape
    if spec.trailing_edge == "sharp":
        half *= 1 - u
    suction = camber + half[:, None] * normals
    pressure = camber - half[:, None] * normals
    return suction, pressure, camber, 2 * half, normals


def normal_offset_curvatures(spec: NACA65SplineSpec, parameter: np.ndarray):
    """Analytic signed side curvature; use the regular sqrt(s) nose limit at s=0."""
    parameter = np.asarray(parameter, dtype=float)
    if np.any((parameter < 0) | (parameter > 1)):
        raise ValueError("曲率参数必须位于 [0, 1]")
    u = np.maximum(parameter, 1e-14)
    ce = CurveEvaluator(spec.camber_curve)
    first, second, third = (ce.evaluate(u, order) for order in (1, 2, 3))
    speed = np.linalg.norm(first, axis=1)
    speed_first = np.sum(first * second, axis=1) / speed
    speed_second = (np.sum(second * second + first * third, axis=1) - speed_first**2) / speed
    tangent = first / speed[:, None]
    tangent_first = second / speed[:, None] - first * (speed_first / speed**2)[:, None]
    tangent_second = (
        third / speed[:, None]
        - 2 * second * (speed_first / speed**2)[:, None]
        - first * (speed_second / speed**2)[:, None]
        + 2 * first * (speed_first**2 / speed**3)[:, None]
    )
    normal, normal_first, normal_second = (
        np.column_stack([-value[:, 1], value[:, 0]])
        for value in (tangent, tangent_first, tangent_second)
    )
    be = CurveEvaluator(spec.thickness_shape_curve)
    b, db, ddb = (be.evaluate(u, order)[:, 0] for order in (0, 1, 2))
    root = np.sqrt(u)
    g = root * (1 - u)
    dg = (1 - 3 * u) / (2 * root)
    ddg = -(1 + 3 * u) / (4 * u * root)
    if spec.trailing_edge == "rounded":
        g, dg, ddg = root, 1 / (2 * root), -1 / (4 * u * root)
    h, dh, ddh = g * b, dg * b + g * db, ddg * b + 2 * dg * db + g * ddb
    offset_first = dh[:, None] * normal + h[:, None] * normal_first
    offset_second = (
        ddh[:, None] * normal + 2 * dh[:, None] * normal_first + h[:, None] * normal_second
    )
    curvatures = []
    nose_speed = np.linalg.norm(ce.evaluate([0.0], 1)[0])
    nose_shape = be.evaluate([0.0])[0, 0]
    for sign in (1, -1):
        d1, d2 = first + sign * offset_first, second + sign * offset_second
        curvature = (d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]) / np.linalg.norm(d1, axis=1) ** 3
        curvature[parameter == 0] = -sign * 2 * nose_speed / nose_shape**2
        curvatures.append(curvature)
    return tuple(curvatures)


def _curvature_knot_jump(spec: NACA65SplineSpec) -> float:
    breaks = np.unique(spec.camber_curve.knots + spec.thickness_shape_curve.knots)
    interior = breaks[1:-1]
    epsilon = 1e-8 * np.minimum(np.diff(breaks)[:-1], np.diff(breaks)[1:])
    left = normal_offset_curvatures(spec, interior - epsilon)
    right = normal_offset_curvatures(spec, interior + epsilon)
    return float(
        max(
            np.max(np.abs(a - b) / np.maximum(1.0, np.maximum(np.abs(a), np.abs(b))))
            for a, b in zip(left, right, strict=True)
        )
    )


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
    tail_speed = np.linalg.norm(ce.evaluate([1.0], 1)[0])
    tail_shape = float(CurveEvaluator(spec.thickness_shape_curve).evaluate([1.0])[0, 0])
    edge_views = {}
    for name, edge_u in (
        ("leading", np.linspace(0.0, np.sqrt(0.06), 501) ** 2),
        ("trailing", np.linspace(0.94, 1.0, 501)),
    ):
        edge_ss, edge_ps, edge_c, _, _ = evaluate_normal_offset(spec, edge_u)
        curvature_ss, curvature_ps = normal_offset_curvatures(spec, edge_u)
        edge_views[name] = {
            "parameter": edge_u.tolist(),
            "suction": edge_ss.tolist(),
            "pressure": edge_ps.tolist(),
            "camber": edge_c.tolist(),
            "suction_curvature": curvature_ss.tolist(),
            "pressure_curvature": curvature_ps.tolist(),
        }
    tail_metadata = {}
    body_suction, body_pressure = suction.copy(), pressure.copy()
    if spec.trailing_edge == "rounded":
        from cascade_forge.domain.compressor_naca65 import round_trailing_edge

        dense_u = 0.5 * (1 - np.cos(np.linspace(0, np.pi, 2001)))
        dense_ss, dense_ps, dense_c, _, _ = evaluate_normal_offset(spec, dense_u)
        suction, pressure, tail_metadata = round_trailing_edge(
            dense_ss, dense_ps, dense_c, dense_u, spec.trailing_edge_radius_ratio, count
        )
        edge_views.update(tail_metadata.pop("edge_views"))
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
        trailing_osculating_circle=tail_metadata.get("trailing_osculating_circle", []),
        edge_metrics={
            "leading_edge_radius_ratio": float(radius),
            "trailing_edge_radius_ratio": spec.trailing_edge_radius_ratio,
            "trailing_edge_tangency_error": tail_metadata.get("trailing_edge_tangency_error", 0.0),
            "trailing_wedge_angle_deg": 0.0
            if spec.trailing_edge == "rounded"
            else float(np.degrees(2 * np.arctan2(tail_shape, tail_speed))),
            "relative_curvature_knot_jump_max": _curvature_knot_jump(spec),
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
            "trailing_edge_osculating_radius": None,
            "edge_views": edge_views,
            "edge_continuity": "G2 joined nose; C2 sides at simple knots; intentional sharp TE",
            "surface_authority": "C(s) +/- sqrt(s)*(1-s)*B(s)*n(s); no surface refit",
            **(
                {
                    "surface_authority": (
                        "C(s) +/- sqrt(s)*B(s)*n(s); local G1 TE blends + circular cap"
                    ),
                    "body_suction": body_suction.tolist(),
                    "body_pressure": body_pressure.tolist(),
                    "normal_invariants_scope": "underlying body before x/c=0.95; cap is separate",
                    "trailing_edge_osculating_radius": spec.trailing_edge_radius_ratio,
                    **tail_metadata,
                }
                if spec.trailing_edge == "rounded"
                else {}
            ),
        },
    )
