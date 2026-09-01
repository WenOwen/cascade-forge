from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import BSpline

from .models_v2 import BSplineCurveSpec


class CurveEvaluator:
    """三次开放夹持 B-spline 位置与解析导数求值器。"""

    def __init__(self, spec: BSplineCurveSpec):
        self.spec = spec
        self.control_points = np.asarray(spec.control_points, dtype=float)
        self.dimension = self.control_points.shape[1]
        self.domain = (float(spec.knots[spec.degree]), float(spec.knots[-spec.degree - 1]))
        self._bspline = BSpline(
            spec.knots,
            self.control_points,
            spec.degree,
            extrapolate=False,
        )

    def evaluate(
        self,
        parameter: np.ndarray | list[float] | float,
        derivative: int = 0,
    ) -> np.ndarray:
        u = np.asarray(parameter, dtype=float)
        if derivative < 0 or derivative > self.spec.degree:
            raise ValueError("导数阶数超出三次 B-spline 合同")
        if np.any(u < self.domain[0] - 1e-12) or np.any(u > self.domain[1] + 1e-12):
            raise ValueError("曲线参数超出有效区间")
        return np.asarray(self._bspline(u, nu=derivative), dtype=float)


def open_uniform_knots(control_count: int, degree: int = 3) -> list[float]:
    if degree != 3:
        raise ValueError("统一内核只使用三次 B-spline")
    if control_count < degree + 1:
        raise ValueError("控制点数量不足")
    internal_count = control_count - degree - 1
    internal = (
        np.linspace(0.0, 1.0, internal_count + 2)[1:-1].tolist()
        if internal_count > 0
        else []
    )
    return [0.0] * (degree + 1) + internal + [1.0] * (degree + 1)


def unit_tangent_and_normal(
    evaluator: CurveEvaluator, u: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    derivative = evaluator.evaluate(u, 1)
    speed = np.linalg.norm(derivative, axis=1)
    if np.any(speed <= 1e-12):
        raise ValueError("曲线存在退化切向")
    tangent = derivative / speed[:, None]
    normal = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    return tangent, normal


def signed_curvature(evaluator: CurveEvaluator, u: np.ndarray | list[float]) -> np.ndarray:
    first = evaluator.evaluate(u, 1)
    second = evaluator.evaluate(u, 2)
    speed = np.linalg.norm(first, axis=1)
    if np.any(speed <= 1e-12):
        raise ValueError("曲线存在退化切向")
    return (first[:, 0] * second[:, 1] - first[:, 1] * second[:, 0]) / speed**3


def angle_between_lines(first: np.ndarray, second: np.ndarray) -> float:
    a = first / max(np.linalg.norm(first), 1e-15)
    b = second / max(np.linalg.norm(second), 1e-15)
    cosine = float(np.clip(abs(np.dot(a, b)), -1.0, 1.0))
    return math.degrees(math.acos(cosine))
