"""NACA TR 1368 Table I blower thickness, with an explicit engineering TE cap.

The published table is preserved before rounding. The last 5% of each side is
joined by a cubic Hermite segment to a semicircle (G1, not G2). This is a
documented manufacturing closure, not a claim to digitize an original test model.
"""

from __future__ import annotations

import numpy as np
from scipy.interpolate import CubicHermiteSpline, CubicSpline

from cascade_forge.domain.models_naca65 import NACA65Geometry, NACA65Spec

STATIONS = (
    np.array(
        [
            0,
            0.5,
            0.75,
            1.25,
            2.5,
            5,
            7.5,
            10,
            15,
            20,
            25,
            30,
            35,
            40,
            45,
            50,
            55,
            60,
            65,
            70,
            75,
            80,
            85,
            90,
            95,
            100,
        ]
    )
    / 100
)
# Half thickness, percent chord: scaled 65(216)-010 + 0.0015*x column.
HALF_THICKNESS = (
    np.array(
        [
            0,
            0.752,
            0.890,
            1.124,
            1.571,
            2.222,
            2.709,
            3.111,
            3.746,
            4.218,
            4.570,
            4.824,
            4.982,
            5.057,
            5.029,
            4.870,
            4.570,
            4.151,
            3.627,
            3.038,
            2.451,
            1.847,
            1.251,
            0.749,
            0.354,
            0.150,
        ]
    )
    / 100
)
PROVENANCE = {
    "source": "NACA TR 1368, Table I, scaled blower thickness column",
    "reference": "NACA RM L51G31 / NACA TR 1368; NACA RM L51J16",
    "thickness_table": "https://digital.library.unt.edu/ark:/67531/metadc60788/m1/6/",
    "nasa_record": "https://ntrs.nasa.gov/citations/19930093730",
    "tail_reference": "https://ntrs.nasa.gov/citations/19930086925",
    "rounding": "engineering G1 Hermite blend from x/c=0.95 + exact semicircle; v1",
}


def compressor_construction(spec: NACA65Spec):
    scale = spec.thickness_ratio / 0.10
    x = np.unique(np.r_[STATIONS, 0.5 * (1 - np.cos(np.linspace(0, np.pi, 1601)))])
    # sqrt(x) resolves the rounded LE and retains the tabulated LE radius.
    half_curve = CubicSpline(
        np.sqrt(STATIONS),
        HALF_THICKNESS * scale,
        bc_type=((1, np.sqrt(2 * 0.00666) * scale), (2, 0)),
    )
    half = half_curve(np.sqrt(x))
    y = np.zeros_like(STATIONS)
    z = STATIONS[1:-1]
    factor = spec.design_lift_coefficient / (4 * np.pi)
    y[1:-1] = -factor * (z * np.log(z) + (1 - z) * np.log1p(-z))
    # The analytic a=1 line has infinite endpoint slopes. Retain its tabulated
    # ordinates and use finite endpoint tangents from the first resolved station.
    slope = factor * np.log((1 - STATIONS[1]) / STATIONS[1])
    mean = CubicSpline(STATIONS, y, bc_type=((1, slope), (1, -slope)))
    camber = np.column_stack([x, mean(x)])
    normal = np.column_stack([-mean(x, 1), np.ones_like(x)])
    normal /= np.linalg.norm(normal, axis=1)[:, None]
    return camber + half[:, None] * normal, camber - half[:, None] * normal, camber, half, normal


def round_trailing_edge(suction, pressure, camber, parameter, radius, count=201):
    """Return complete sides LE -> common TE nose, plus auditable cap geometry."""
    from cascade_forge.domain.naca65 import _resample_surface

    u = np.asarray(parameter)
    c = CubicSpline(u, camber)
    curves = [CubicSpline(u, points) for points in (suction, pressure)]
    join = float(np.interp(0.95, np.asarray(camber)[:, 0], u))
    tangent = c(1.0, 1)
    tangent /= np.linalg.norm(tangent)
    normal = np.array([-tangent[1], tangent[0]])
    nose = c(1.0)
    center = nose - radius * tangent
    angle = np.linspace(np.pi / 2, -np.pi / 2, 161)
    arc = center + radius * (np.cos(angle)[:, None] * tangent + np.sin(angle)[:, None] * normal)
    arc[80] = nose
    results, dense_sides = [], []
    tangency_errors = []
    for sign, curve in zip((1, -1), curves, strict=True):
        contact = center + sign * radius * normal
        span = float(np.linalg.norm(contact - curve(join)))
        blend = CubicHermiteSpline(
            [0.0, 1.0],
            [curve(join), contact],
            [curve(join, 1) * (1 - join), tangent * span],
        )
        half_arc = arc[:81] if sign == 1 else arc[80:][::-1]
        dense = np.vstack(
            [
                curve(np.linspace(0, np.sqrt(join), 1801) ** 2),
                blend(np.linspace(0, 1, 401)[1:]),
                half_arc[1:],
            ]
        )
        dense_sides.append(dense)
        sample = 0.5 * (1 - np.cos(np.linspace(0, np.pi, count)))
        results.append(_resample_surface(dense, sample))
        derivative = blend(1.0, 1)
        tangency_errors.append(abs(float(np.dot(derivative / np.linalg.norm(derivative), normal))))
    circle_angles = np.linspace(0, 2 * np.pi, 161)
    circle = center + radius * np.column_stack([np.cos(circle_angles), np.sin(circle_angles)])
    return (
        *results,
        {
            "trailing_edge_radius_ratio": float(radius),
            "trailing_edge_center": center.tolist(),
            "trailing_edge_nose": nose.tolist(),
            "trailing_edge_arc": arc.tolist(),
            "trailing_osculating_circle": circle.tolist(),
            "trailing_edge_contacts": [arc[0].tolist(), arc[-1].tolist()],
            "trailing_edge_tangency_error": max(tangency_errors),
            "trailing_edge_blend_start_x_ratio": 0.95,
            "trailing_edge_kind": "rounded; exact circular cap with G1 side blends",
            "edge_continuity": "G1 at tail blends and circle contacts; curvature may jump",
            "edge_views": {
                "trailing": {
                    "suction": dense_sides[0][-600:].tolist(),
                    "pressure": dense_sides[1][-600:].tolist(),
                }
            },
        },
    )


def generate_compressor(spec: NACA65Spec) -> NACA65Geometry:
    from cascade_forge.domain.naca65 import _bessel_interpolate

    ss, ps, c, h, n = compressor_construction(spec)
    u = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(c, axis=0), axis=1))]
    u /= u[-1]
    sample = 0.5 * (1 - np.cos(np.linspace(0, np.pi, spec.points_per_side)))
    radius = 0.0015 * spec.thickness_ratio / 0.10
    rounded_ss, rounded_ps, edge = round_trailing_edge(ss, ps, c, u, radius)
    return NACA65Geometry(
        native_id=spec.native_id,
        designation=spec.canonical_designation,
        spec=spec,
        raw_suction=ss.tolist(),
        raw_pressure=ps.tolist(),
        normalized_suction=rounded_ss.tolist(),
        normalized_pressure=rounded_ps.tolist(),
        camber=np.column_stack([_bessel_interpolate(u, c[:, j], sample) for j in (0, 1)]).tolist(),
        thickness=(2 * _bessel_interpolate(u, h, sample)).tolist(),
        construction_camber=c.tolist(),
        construction_half_thickness=h.tolist(),
        construction_normals=n.tolist(),
        construction_parameter=u.tolist(),
        parameter=sample.tolist(),
        source_provenance=PROVENANCE,
        metadata={
            **edge,
            "raw_trailing_edge_gap_ratio": float(2 * h[-1]),
            "thickness_definition": "nominal table scale; 10% table peak is 10.114% chord",
            "trailing_edge_policy": (
                "raw table construction preserved; normalized includes engineering round cap"
            ),
            "camber_policy": "a=1 table ordinates; cubic interpolation with finite endpoint slopes",
            "thickness_application": "normal-to-mean-line before local TE blending",
            "table_stations": STATIONS.tolist(),
            "table_half_thickness": (HALF_THICKNESS * spec.thickness_ratio / 0.10).tolist(),
            "leading_edge_radius_ratio": 0.00666 * (spec.thickness_ratio / 0.10) ** 2,
        },
    )
