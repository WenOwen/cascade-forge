from __future__ import annotations

import math
import re

import numpy as np
from scipy.interpolate import CubicHermiteSpline

from .models_naca65 import (
    NACA65Family,
    NACA65Geometry,
    NACA65InputMode,
    NACA65Spec,
)
from .naca65_tables import load_tables

OPENVSP_COMMIT = "51bdec01d9a50fa4bdbc960b0def21dcd6330f72"
SIXSERIES_C_SHA256 = "e41a48399647340fa09eb4f015a1d119a0da8d7959f6cfc43badea849964c342"
SIXSERIES_FORTRAN_SHA256 = "59f5caf05868e9cb15accba897396f3f24b90837540e3980e4cc7f261b2c232d"
SOURCE_PROVENANCE = {
    "source": "OpenVSP NASA sixseries",
    "repository": "https://github.com/OpenVSP/OpenVSP",
    "commit": OPENVSP_COMMIT,
    "fortran_sha256": SIXSERIES_FORTRAN_SHA256,
    "c_sha256": SIXSERIES_C_SHA256,
    "reference": "NASA TM X-3069 (1974)",
}


def parse_naca65_designation(designation: str) -> NACA65Spec:
    """Parse compressor-style 65-010, 65-410 or 65-(12)10 designations."""

    compact = re.sub(r"\s+", "", designation.upper()).removeprefix("NACA")
    match = re.fullmatch(r"65-(?:\((\d{2})\)|(\d))(\d{2})", compact)
    if not match:
        raise ValueError("压气机编号应为 65-010、65-410、65-810 或 65-(12)10 形式")
    lift_digits = match.group(1) or match.group(2)
    thickness_percent = int(match.group(3))
    return NACA65Spec(
        family=NACA65Family.SERIES_65,
        thickness_ratio=thickness_percent / 100.0,
        design_lift_coefficient=int(lift_digits) / 10.0,
        loading_parameter_a=1.0,
        input_mode=NACA65InputMode.COMPRESSOR_PRESET,
        preset_designation=designation,
    )


def generate_naca65(spec: NACA65Spec | str) -> NACA65Geometry:
    """Generate a native NACA 65/65A profile with the NASA sixseries method."""

    if isinstance(spec, str):
        spec = parse_naca65_designation(spec)
    raw_suction, raw_pressure, native_camber, native_half, native_normals = _sixseries_construction(
        spec
    )
    normalized_suction, normalized_pressure = _normalize_and_resample(
        raw_suction, raw_pressure, spec.points_per_side
    )
    # Keep the original paired construction stations. Equal *surface* arc fractions
    # are not corresponding normal feet and must never define a mean line.
    parameter = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(native_camber, axis=0), axis=1))]
    parameter /= parameter[-1]
    sample = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, spec.points_per_side)))
    camber = np.column_stack(
        [_bessel_interpolate(parameter, native_camber[:, axis], sample) for axis in range(2)]
    )
    thickness = 2 * _bessel_interpolate(parameter, native_half, sample)
    raw_gap = float(np.linalg.norm(raw_suction[-1] - raw_pressure[-1]))
    return NACA65Geometry(
        native_id=spec.native_id,
        designation=spec.canonical_designation,
        spec=spec,
        raw_suction=raw_suction.tolist(),
        raw_pressure=raw_pressure.tolist(),
        normalized_suction=normalized_suction.tolist(),
        normalized_pressure=normalized_pressure.tolist(),
        camber=camber.tolist(),
        thickness=thickness.tolist(),
        construction_camber=native_camber.tolist(),
        construction_half_thickness=native_half.tolist(),
        construction_normals=native_normals.tolist(),
        construction_parameter=parameter.tolist(),
        parameter=sample.tolist(),
        source_provenance=SOURCE_PROVENANCE,
        metadata={
            "raw_points_per_side": len(raw_suction),
            "normalized_points_per_side": spec.points_per_side,
            "raw_trailing_edge_gap_ratio": raw_gap,
            "trailing_edge_policy": "raw-preserved; openvsp-compatible shared endpoint",
            "thickness_application": "normal-to-mean-line",
            "decomposition_version": "sixseries-stations-v2",
            "surface_resampling": "display-only; independent of construction stations",
            "65a_tail_rule": "native straight-tail override retained in raw reference only",
        },
    )


def _sixseries_coordinates(spec: NACA65Spec) -> tuple[np.ndarray, np.ndarray]:
    return _sixseries_construction(spec)[:2]


def _sixseries_construction(spec: NACA65Spec) -> tuple[np.ndarray, ...]:
    family_a = spec.family == NACA65Family.SERIES_65A
    tables = load_tables()
    phi_grid = np.linspace(0.0, math.pi, 201)
    eps_name = "phep65a" if family_a else "phep65"
    psi_name = "phps65a" if family_a else "phps65"
    eps = _bessel_interpolate(*tables[eps_name], phi_grid)
    psi = _bessel_interpolate(*tables[psi_name], phi_grid)
    xt, yt, _, _ = _thickness_distribution(phi_grid[:-1], eps[:-1], psi[:-1], spec.thickness_ratio)
    stations = _sixseries_stations()
    half_thickness = _bessel_interpolate(xt, yt, stations)
    camber, camber_slope, _ = _mean_line(
        stations,
        spec.design_lift_coefficient,
        spec.loading_parameter_a,
        modified=family_a,
    )

    tangent_scale = np.sqrt(1.0 + camber_slope**2)
    sin_theta = camber_slope / tangent_scale
    cos_theta = 1.0 / tangent_scale
    suction = np.column_stack(
        [stations - half_thickness * sin_theta, camber + half_thickness * cos_theta]
    )
    pressure = np.column_stack(
        [stations + half_thickness * sin_theta, camber - half_thickness * cos_theta]
    )
    suction[0] = 0.0
    pressure[0] = 0.0

    if family_a:
        # The NASA 6A construction replaces both surfaces with straight
        # segments aft of the first native station in [0.815, 0.825].
        pivot = int(np.flatnonzero((stations >= 0.815) & (stations <= 0.825))[0])
        for surface in (suction, pressure):
            slope = -surface[pivot, 1] / (1.0 - surface[pivot, 0])
            intercept = -slope
            surface[pivot:, 1] = slope * surface[pivot:, 0] + intercept

    return (
        suction,
        pressure,
        np.column_stack([stations, camber]),
        half_thickness,
        np.column_stack([-sin_theta, cos_theta]),
    )


def _thickness_distribution(
    phi: np.ndarray,
    eps: np.ndarray,
    psi: np.ndarray,
    target_thickness: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    accumulated = 1.0
    scale = 1.0
    xt = np.empty_like(phi)
    yt = np.empty_like(phi)
    ytp = np.empty_like(phi)
    ytpp = np.empty_like(phi)
    x_at_max = 0.0
    for _ in range(11):
        accumulated *= scale
        xt = -2.0 * np.cosh(psi * accumulated) * np.cos(phi - eps * accumulated)
        yt = 2.0 * np.sinh(psi * accumulated) * np.sin(phi - eps * accumulated)
        ytp, ytpp = _finite_derivatives(xt, yt)
        crossing = np.flatnonzero((ytp[1:] < 0.0) & (ytp[:-1] >= 0.0))
        if len(crossing):
            index = int(crossing[-1])
            x_at_max = xt[index] + ytp[index] * (xt[index + 1] - xt[index]) / (
                ytp[index] - ytp[index + 1]
            )
        maximum = float(_bessel_interpolate(xt, yt, np.asarray([x_at_max]))[0])
        chord = float(xt[-1] - xt[0])
        realized = 2.0 * maximum / chord
        scale = target_thickness / realized if target_thickness > 1e-11 else 1.0
        if target_thickness <= 1e-11 or abs(scale - 1.0) <= 1e-4:
            break

    chord = float(xt[-1] - xt[0])
    linear_scale = scale
    xt = (xt - xt[0]) / chord
    yt = linear_scale * yt / chord
    ytp = linear_scale * ytp
    ytpp = linear_scale * ytpp * chord

    if target_thickness > 1e-11:
        # Tilted ellipse through the eleventh mapped point, matching the
        # original leading-edge regularization.
        j = 10
        cn = 2.0 * ytp[j] - yt[j] / xt[j] + 0.1
        an = xt[j] * (ytp[j] * xt[j] - yt[j]) / (xt[j] * (2.0 * ytp[j] - cn) - yt[j])
        bn = math.sqrt((yt[j] - cn * xt[j]) ** 2 / (1.0 - (xt[j] - an) ** 2 / an**2))
        for index in range(10):
            root = max(0.0, 1.0 - (xt[index] - an) ** 2 / an**2)
            yt[index] = bn * math.sqrt(root) + cn * xt[index]
            if xt[index] > 1e-11:
                ytp[index] = bn**2 * (an - xt[index]) / an**2 / (yt[index] - cn * xt[index]) + cn
                ytpp[index] = -(bn**4) / an**2 / (yt[index] - cn * xt[index]) ** 3
    return xt, yt, ytp, ytpp


def _mean_line(
    x: np.ndarray,
    design_lift: float,
    a: float,
    *,
    modified: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    y = np.zeros_like(x)
    slope = np.zeros_like(x)
    second = np.zeros_like(x)
    if abs(design_lift) <= 1e-15:
        return y, slope, second

    tiny = 1e-11
    if a <= tiny:
        g, h, q = -0.25, -0.5, 1.0
    elif abs(a - 1.0) <= tiny:
        g, h, q = 0.0, 0.0, 0.0
    else:
        g = -(a * a * (0.5 * math.log(a) - 0.25) + 0.25) / (1.0 - a)
        h = (0.5 * (1.0 - a) ** 2 * math.log(1.0 - a) - 0.25 * (1.0 - a) ** 2) / (1.0 - a) + g
        q = 1.0

    factor = design_lift / math.pi / (a + 1.0) / 2.0
    for index, value in enumerate(x):
        if value <= 0.0:
            probe = 0.005
            z1, _ = _mean_line_z_derivatives(probe, a)
            slope[index] = factor * (z1 / (1.0 - q * a) - 1.0 - math.log(probe) - h)
            continue
        probe = min(float(value), 1.0 - 1e-10)
        z, _, _ = _mean_line_z(probe, a)
        derivative_probe = max(0.005, probe)
        _, z1, z2 = _mean_line_z(derivative_probe, a)
        y[index] = factor * (z / (1.0 - q * a) - probe * math.log(probe) + g - h * probe)
        slope[index] = factor * (z1 / (1.0 - q * a) - 1.0 - math.log(derivative_probe) - h)
        second[index] = 0.0 if probe <= 0.005 else factor * (z2 / (1.0 - q * a) - 1.0 / probe)
    y[-1] = 0.0

    if modified:
        y *= 0.97948
        slope *= 0.97948
        second *= 0.97948
        limiting_slope = -0.24521 * design_lift
        mask = slope <= limiting_slope
        triggered = np.flatnonzero(mask)
        if len(triggered):
            mask[int(triggered[0]) :] = True
        y[mask] = 0.24521 * design_lift * (1.0 - x[mask])
        slope[mask] = limiting_slope
        second[mask] = 0.0
    return y, slope, second


def _mean_line_z(x: float, a: float) -> tuple[float, float, float]:
    tiny = 1e-11
    if abs(a - 1.0) <= tiny:
        z = -(1.0 - x) * math.log(1.0 - x)
        return z, math.log(1.0 - x) + 1.0, -1.0 / (1.0 - x)
    amx = a - x
    omx = 1.0 - x
    am_log = amx * math.log(abs(amx)) if abs(amx) > tiny else 0.0
    om_log = omx * math.log(omx)
    z = 0.5 * amx * am_log - 0.5 * omx * om_log - 0.25 * amx**2 + 0.25 * omx**2
    if abs(amx) <= tiny:
        z1 = -0.5 * omx * (-math.log(omx) - 1.0) + 0.5 * omx * math.log(omx) - 0.5 * omx
        z2 = -math.log(omx) - 0.5
    else:
        am_log_derivative = -math.log(abs(amx)) - 1.0
        om_log_derivative = -math.log(omx) - 1.0
        z1 = 0.5 * (amx * am_log_derivative - am_log - omx * om_log_derivative + om_log + amx - omx)
        z2 = 0.5 * amx / amx - am_log_derivative - 0.5 * omx / omx + om_log_derivative
    return z, z1, z2


def _mean_line_z_derivatives(x: float, a: float) -> tuple[float, float]:
    _, first, second = _mean_line_z(x, a)
    return first, second


def _sixseries_stations() -> np.ndarray:
    values = [0.0]
    x = 0.00025
    while x <= 1.0 + 1e-9:
        values.append(min(x, 1.0))
        fraction = 1.0
        if x <= 0.0975 + 1e-12:
            fraction = 0.25
        if x <= 0.01225 + 1e-12:
            fraction = 0.025
        x += fraction * 0.01
    return np.asarray(values, dtype=float)


def _bessel_interpolate(x: np.ndarray, y: np.ndarray, target: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    h = np.diff(x)
    delta = np.diff(y) / h
    derivatives = np.empty_like(y)
    derivatives[0] = (-h[0] / (h[0] + h[1])) * delta[1] + (1.0 + h[0] / (h[0] + h[1])) * delta[0]
    derivatives[-1] = (-h[-1] / (h[-1] + h[-2])) * delta[-2] + (
        1.0 + h[-1] / (h[-1] + h[-2])
    ) * delta[-1]
    derivatives[1:-1] = (h[1:] * delta[:-1] + h[:-1] * delta[1:]) / (h[:-1] + h[1:])
    return np.asarray(CubicHermiteSpline(x, y, derivatives)(target), dtype=float)


def _finite_derivatives(x: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    first = np.empty_like(values)
    second = np.empty_like(values)
    for index in range(1, len(x) - 1):
        dx1 = x[index] - x[index - 1]
        dx2 = x[index + 1] - x[index]
        delta1 = (values[index] - values[index - 1]) / dx1
        delta2 = (values[index + 1] - values[index]) / dx2
        divisor = 1.0 / (dx1 + dx2)
        first[index] = dx2 * divisor * delta1 + dx1 * divisor * delta2
        second[index] = 2.0 * (delta2 - delta1) * divisor
    for index, step in ((0, 1), (len(x) - 1, -1)):
        first_neighbor = index + step
        second_neighbor = first_neighbor + step
        dx1 = x[first_neighbor] - x[index]
        delta1 = (values[first_neighbor] - values[index]) / dx1
        dx2 = x[second_neighbor] - x[first_neighbor]
        delta2 = (values[second_neighbor] - values[first_neighbor]) / dx2
        divisor = 1.0 / (dx1 + dx2)
        weight = -dx1 * divisor
        first[index] = weight * delta2 + (1.0 - weight) * delta1
        second[index] = 2.0 * (delta2 - delta1) * divisor
    return first, second


def _normalize_and_resample(
    suction: np.ndarray,
    pressure: np.ndarray,
    count: int,
) -> tuple[np.ndarray, np.ndarray]:
    suction = suction.copy()
    pressure = pressure.copy()
    trailing_y = 0.5 * (suction[-1, 1] + pressure[-1, 1])
    suction[-1] = [1.0, trailing_y]
    pressure[-1] = [1.0, trailing_y]
    target = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, count)))
    return _resample_surface(suction, target), _resample_surface(pressure, target)


def _resample_surface(surface: np.ndarray, target: np.ndarray) -> np.ndarray:
    distance = np.linalg.norm(np.diff(surface, axis=0), axis=1)
    parameter = np.concatenate([[0.0], np.cumsum(distance)])
    parameter /= parameter[-1]
    x = _bessel_interpolate(parameter, surface[:, 0], target)
    y = _bessel_interpolate(parameter, surface[:, 1], target)
    result = np.column_stack([x, y])
    result[0] = [0.0, 0.0]
    result[-1] = surface[-1]
    return result
