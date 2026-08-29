from __future__ import annotations

import math

import numpy as np
from scipy.integrate import cumulative_trapezoid
from scipy.interpolate import CubicSpline
from scipy.optimize import brentq

from .models import AirfoilGeometry, GeometrySpec, Handedness


def _angle_schedule(spec: GeometrySpec, x: np.ndarray) -> tuple[np.ndarray, float]:
    inlet = spec.inlet_metal_angle_deg
    outlet = spec.outlet_metal_angle_deg
    knots = np.array([0.0, 0.25, 0.50, 0.75, 1.0])
    values = np.array(
        [
            inlet,
            inlet + 0.25 * (outlet - inlet) + spec.quarter_angle_bias_deg,
            0.5 * (inlet + outlet),
            inlet + 0.75 * (outlet - inlet) + spec.three_quarter_angle_bias_deg,
            outlet,
        ]
    )
    spline = CubicSpline(knots, values, bc_type="natural")
    beta_global = spline(x)

    def closure(stagger_deg: float) -> float:
        slopes = np.tan(np.radians(beta_global - stagger_deg))
        return float(np.trapezoid(slopes, x))

    # 在不跨越 tan 奇点的角度支路内，closure 对 stagger 严格单调递减；
    # 根必定位于 beta_global 的最小值与最大值之间。因此无需逐叶型扫描宽区间。
    lower = float(np.min(beta_global))
    upper = float(np.max(beta_global))
    if upper - lower >= 89.0:
        raise ValueError("角度样条跨度过大，无法在单一正切支路内闭合")
    if math.isclose(lower, upper, abs_tol=1e-13):
        stagger = lower
    else:
        stagger = float(brentq(closure, lower, upper, xtol=1e-12))
    local_angle = beta_global - stagger
    return local_angle, stagger


def _quintic_hermite_coefficients(
    y0: float,
    dy0: float,
    d2y0: float,
    y1: float,
    dy1: float,
    d2y1: float,
) -> np.ndarray:
    coefficients = np.zeros(6)
    coefficients[:3] = [y0, dy0, 0.5 * d2y0]
    matrix = np.array([[1, 1, 1], [3, 4, 5], [6, 12, 20]], dtype=float)
    rhs = np.array(
        [
            y1 - coefficients[0] - coefficients[1] - coefficients[2],
            dy1 - coefficients[1] - 2 * coefficients[2],
            d2y1 - 2 * coefficients[2],
        ]
    )
    coefficients[3:] = np.linalg.solve(matrix, rhs)
    return coefficients


def _half_thickness(spec: GeometrySpec, x: np.ndarray) -> np.ndarray:
    xt = spec.max_thickness_x_ratio
    hmax = 0.5 * spec.max_thickness_ratio
    hte = 0.5 * spec.trailing_edge_thickness_ratio
    radius = spec.leading_edge_radius_ratio

    # 前段在 q=sqrt(x) 中构造四次多项式；首项严格给出有限前缘半径。
    qt = math.sqrt(xt)
    a1 = math.sqrt(2.0 * radius)
    kmax = -spec.fore_shape_factor * hmax / max(xt * (1.0 - xt), 0.04)
    matrix = np.array(
        [
            [qt**2, qt**3, qt**4],
            [2 * qt, 3 * qt**2, 4 * qt**3],
            [2.0, 6 * qt, 12 * qt**2],
        ]
    )
    rhs = np.array([hmax - a1 * qt, -a1, 4.0 * qt**2 * kmax])
    a2, a3, a4 = np.linalg.solve(matrix, rhs)

    thickness = np.empty_like(x)
    fore = x <= xt
    q = np.sqrt(np.maximum(x[fore], 0.0))
    thickness[fore] = a1 * q + a2 * q**2 + a3 * q**3 + a4 * q**4

    length = 1.0 - xt
    slope_te = -math.tan(math.radians(spec.trailing_edge_half_wedge_deg))
    d2_te = spec.aft_shape_factor * (hmax - hte) / max(length**2, 1e-8)
    coefficients = _quintic_hermite_coefficients(
        hmax,
        0.0,
        kmax * length**2,
        hte,
        slope_te * length,
        d2_te * length**2,
    )
    s = (x[~fore] - xt) / length
    powers = np.vstack([s**index for index in range(6)])
    thickness[~fore] = coefficients @ powers
    thickness[0] = 0.0
    thickness[-1] = hte
    return thickness


def generate_profile(spec: GeometrySpec) -> AirfoilGeometry:
    """由角度样条中弧线和 C2 厚度分布生成弦长归一化叶型。"""

    dense_count = max(1001, spec.points_per_side * 4 + 1)
    beta = np.linspace(0.0, math.pi, dense_count)
    x_dense = 0.5 * (1.0 - np.cos(beta))
    local_angle, stagger = _angle_schedule(spec, x_dense)
    slope = np.tan(np.radians(local_angle))
    y_dense = cumulative_trapezoid(slope, x_dense, initial=0.0)
    y_dense -= x_dense * y_dense[-1]
    half_dense = _half_thickness(spec, x_dense)
    if not np.all(np.isfinite(half_dense)):
        raise ValueError("厚度模型产生非有限值")

    target_beta = np.linspace(0.0, math.pi, spec.points_per_side)
    target_x = 0.5 * (1.0 - np.cos(target_beta))
    target_y = np.interp(target_x, x_dense, y_dense)
    target_half = np.interp(target_x, x_dense, half_dense)

    sign = 1.0 if spec.handedness == Handedness.RIGHT else -1.0
    target_y *= sign
    suction = np.column_stack([target_x, target_y + target_half])
    pressure = np.column_stack([target_x, target_y - target_half])
    camber = np.column_stack([target_x, target_y])

    return AirfoilGeometry(
        geometry_id=spec.geometry_id,
        spec=spec,
        origin="generated",
        suction=suction.tolist(),
        pressure=pressure.tolist(),
        camber=camber.tolist(),
        thickness=(2.0 * target_half).tolist(),
        realized_stagger_deg=stagger * sign,
        metadata={
            "outlet_metal_angle_deg": spec.outlet_metal_angle_deg * sign,
            "mathematical_kernel": "angle-spline + q-leading-edge/quintic-aft-thickness",
        },
    )
