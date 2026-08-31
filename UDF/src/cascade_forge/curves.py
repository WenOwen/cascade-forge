from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import BSpline
from scipy.special import comb

from .models_v2 import (
    BezierCurveSpec,
    BSplineCurveSpec,
    CurveSpec,
    NURBSCurveSpec,
)


class CurveEvaluator:
    """统一的 Bezier、B-spline 和 NURBS 解析求值器。"""

    def __init__(self, spec: CurveSpec):
        self.spec = spec
        self.control_points = np.asarray(spec.control_points, dtype=float)
        self.dimension = self.control_points.shape[1]
        if isinstance(spec, (BSplineCurveSpec, NURBSCurveSpec)):
            self.domain = (float(spec.knots[spec.degree]), float(spec.knots[-spec.degree - 1]))
        else:
            self.domain = (0.0, 1.0)

    def evaluate(self, parameter: np.ndarray | float, derivative: int = 0) -> np.ndarray:
        u = np.asarray(parameter, dtype=float)
        if np.any(u < self.domain[0] - 1e-12) or np.any(u > self.domain[1] + 1e-12):
            raise ValueError("曲线参数超出有效区间")
        if isinstance(self.spec, BezierCurveSpec):
            return self._bezier(u, derivative)
        if isinstance(self.spec, BSplineCurveSpec):
            spline = BSpline(
                self.spec.knots,
                self.control_points,
                self.spec.degree,
                extrapolate=False,
            )
            return np.asarray(spline(u, nu=derivative), dtype=float)
        return self._nurbs(u, derivative)

    def _bezier(self, u: np.ndarray, derivative: int) -> np.ndarray:
        controls = self.control_points
        degree = self.spec.degree
        if derivative > degree:
            shape = u.shape + (self.dimension,)
            return np.zeros(shape)
        for _ in range(derivative):
            controls = degree * np.diff(controls, axis=0)
            degree -= 1
        flat = u.reshape(-1)
        basis = np.column_stack(
            [comb(degree, i) * flat**i * (1.0 - flat) ** (degree - i) for i in range(degree + 1)]
        )
        result = basis @ controls
        return result.reshape(u.shape + (self.dimension,))

    def _nurbs(self, u: np.ndarray, derivative: int) -> np.ndarray:
        assert isinstance(self.spec, NURBSCurveSpec)
        weights = np.asarray(self.spec.weights, dtype=float)
        weighted = self.control_points * weights[:, None]
        numerator = BSpline(self.spec.knots, weighted, self.spec.degree, extrapolate=False)
        denominator = BSpline(self.spec.knots, weights, self.spec.degree, extrapolate=False)
        a0 = np.asarray(numerator(u), dtype=float)
        w0 = np.asarray(denominator(u), dtype=float)
        if derivative == 0:
            return a0 / w0[..., None]
        a1 = np.asarray(numerator(u, nu=1), dtype=float)
        w1 = np.asarray(denominator(u, nu=1), dtype=float)
        first = a1 / w0[..., None] - a0 * w1[..., None] / w0[..., None] ** 2
        if derivative == 1:
            return first
        if derivative > 2:
            raise ValueError("NURBS 当前支持到二阶解析导数")
        a2 = np.asarray(numerator(u, nu=2), dtype=float)
        w2 = np.asarray(denominator(u, nu=2), dtype=float)
        return (
            a2 / w0[..., None]
            - a0 * w2[..., None] / w0[..., None] ** 2
            - 2.0 * a1 * w1[..., None] / w0[..., None] ** 2
            + 2.0 * a0 * w1[..., None] ** 2 / w0[..., None] ** 3
        )


def open_uniform_knots(control_count: int, degree: int) -> list[float]:
    internal_count = control_count - degree - 1
    internal = (
        np.linspace(0.0, 1.0, internal_count + 2)[1:-1].tolist() if internal_count > 0 else []
    )
    return [0.0] * (degree + 1) + internal + [1.0] * (degree + 1)


def unit_tangent_and_normal(
    evaluator: CurveEvaluator, u: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    derivative = evaluator.evaluate(u, 1)
    speed = np.linalg.norm(derivative, axis=1)
    if np.any(speed <= 1e-12):
        raise ValueError("中弧线存在退化切向")
    tangent = derivative / speed[:, None]
    normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    return tangent, normal


def angle_between_lines(first: np.ndarray, second: np.ndarray) -> float:
    a = first / max(np.linalg.norm(first), 1e-15)
    b = second / max(np.linalg.norm(second), 1e-15)
    cosine = float(np.clip(abs(np.dot(a, b)), -1.0, 1.0))
    return math.degrees(math.acos(cosine))
