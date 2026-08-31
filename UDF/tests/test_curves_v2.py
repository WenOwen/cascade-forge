from __future__ import annotations

import numpy as np

from cascade_forge.curves import CurveEvaluator, open_uniform_knots
from cascade_forge.models_v2 import (
    BezierCurveSpec,
    BSplineCurveSpec,
    NURBSCurveSpec,
)


def test_bezier_endpoints_and_derivative() -> None:
    curve = BezierCurveSpec(
        degree=3,
        control_points=[[0.0, 0.0], [0.2, 0.3], [0.7, 0.2], [1.0, 0.0]],
    )
    evaluator = CurveEvaluator(curve)
    np.testing.assert_allclose(evaluator.evaluate([0.0, 1.0]), [[0.0, 0.0], [1.0, 0.0]])
    np.testing.assert_allclose(evaluator.evaluate([0.0], 1)[0], [0.6, 0.9])


def test_open_clamped_spline_endpoints() -> None:
    controls = [[0.0, 0.0], [0.2, 0.2], [0.4, 0.3], [0.7, 0.2], [0.9, 0.1], [1.0, 0.0]]
    knots = open_uniform_knots(len(controls), 3)
    curve = BSplineCurveSpec(degree=3, control_points=controls, knots=knots)
    np.testing.assert_allclose(
        CurveEvaluator(curve).evaluate([0.0, 1.0]),
        [controls[0], controls[-1]],
        atol=1e-14,
    )


def test_nurbs_all_one_weights_equal_bspline_and_scale_invariant() -> None:
    controls = [[0.0, 0.0], [0.2, 0.2], [0.4, 0.3], [0.7, 0.2], [0.9, 0.1], [1.0, 0.0]]
    knots = open_uniform_knots(len(controls), 3)
    spline = BSplineCurveSpec(degree=3, control_points=controls, knots=knots)
    nurbs_one = NURBSCurveSpec(
        degree=3, control_points=controls, knots=knots, weights=[1.0] * len(controls)
    )
    nurbs_scaled = nurbs_one.model_copy(update={"weights": [3.0] * len(controls)})
    u = np.linspace(0.0, 1.0, 101)
    expected = CurveEvaluator(spline).evaluate(u)
    np.testing.assert_allclose(CurveEvaluator(nurbs_one).evaluate(u), expected, atol=1e-13)
    np.testing.assert_allclose(CurveEvaluator(nurbs_scaled).evaluate(u), expected, atol=1e-13)
    np.testing.assert_allclose(
        CurveEvaluator(nurbs_scaled).evaluate(u, 1),
        CurveEvaluator(nurbs_one).evaluate(u, 1),
        atol=1e-12,
    )
