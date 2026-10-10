from __future__ import annotations

import numpy as np
import pytest

from cascade_forge.curves import (
    CurveEvaluator,
    open_uniform_knots,
    signed_curvature,
    unit_tangent_and_normal,
)
from cascade_forge.models_v2 import BSplineCurveSpec


def _curve() -> BSplineCurveSpec:
    controls = [
        [0.0, 0.0],
        [0.12, 0.18],
        [0.32, 0.26],
        [0.58, 0.20],
        [0.82, 0.08],
        [1.0, 0.0],
    ]
    return BSplineCurveSpec(control_points=controls, knots=open_uniform_knots(len(controls)))


def test_open_clamped_bspline_endpoints_and_analytic_derivatives() -> None:
    curve = _curve()
    evaluator = CurveEvaluator(curve)
    np.testing.assert_allclose(
        evaluator.evaluate([0.0, 1.0]),
        [curve.control_points[0], curve.control_points[-1]],
        atol=1e-14,
    )

    parameter = np.array([0.17, 0.43, 0.79])
    step = 1e-6
    finite_difference = (
        evaluator.evaluate(parameter + step) - evaluator.evaluate(parameter - step)
    ) / (2.0 * step)
    np.testing.assert_allclose(
        evaluator.evaluate(parameter, 1), finite_difference, rtol=2e-8, atol=2e-9
    )


def test_unit_normal_and_signed_curvature_are_finite() -> None:
    evaluator = CurveEvaluator(_curve())
    parameter = np.linspace(0.0, 1.0, 101)
    tangent, normal = unit_tangent_and_normal(evaluator, parameter)
    np.testing.assert_allclose(np.linalg.norm(tangent, axis=1), 1.0, atol=1e-13)
    np.testing.assert_allclose(np.linalg.norm(normal, axis=1), 1.0, atol=1e-13)
    np.testing.assert_allclose(np.sum(tangent * normal, axis=1), 0.0, atol=1e-13)
    assert np.all(np.isfinite(signed_curvature(evaluator, parameter)))


def test_unified_kernel_rejects_non_cubic_or_non_clamped_splines() -> None:
    with pytest.raises(ValueError, match="三次"):
        open_uniform_knots(6, degree=2)
    with pytest.raises(ValueError, match="开放夹持"):
        BSplineCurveSpec(
            control_points=[[0.0], [0.1], [0.2], [0.3]],
            knots=[0.0, 0.0, 0.0, 0.1, 0.9, 1.0, 1.0, 1.0],
        )
