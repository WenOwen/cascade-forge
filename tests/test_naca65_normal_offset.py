from __future__ import annotations

import json

import numpy as np
import pytest

from cascade_forge.domain.curves import CurveEvaluator, unit_tangent_and_normal
from cascade_forge.domain.fitting_naca65 import point_to_polyline
from cascade_forge.domain.models_naca65 import NACA65BenchmarkConfig, NACA65Spec, NACA65SplineSpec
from cascade_forge.domain.naca65 import generate_naca65
from cascade_forge.domain.normal_bspline import (
    evaluate_normal_offset,
    fit_decomposition,
    generate_normal_offset_profile,
    normal_offset_curvatures,
)
from cascade_forge.storage.naca65 import NACA65BenchmarkStore
from cascade_forge.workbench.benchmark_naca65 import benchmark_specs


@pytest.mark.parametrize("native_spec", benchmark_specs(NACA65BenchmarkConfig()))
def test_all_36_profiles_preserve_normal_decomposition(native_spec):
    ref = generate_naca65(native_spec)
    if native_spec.family.value == "65":
        # This invariant uses original corresponding stations, NOT independently
        # resampled SS/PS indices. It detects the original false-mean-line bug.
        raw_mid = 0.5 * (np.asarray(ref.raw_suction) + np.asarray(ref.raw_pressure))
        np.testing.assert_allclose(raw_mid, ref.construction_camber, atol=1e-12)
    spec, result = fit_decomposition(ref)
    assert result.success
    assert spec.camber_curve.degree == 5
    assert spec.thickness_shape_curve.degree == 3
    u = np.linspace(0, 1, 1503)
    ss, ps, c, t, n = evaluate_normal_offset(spec, u)
    tangent, _ = unit_tangent_and_normal(CurveEvaluator(spec.camber_curve), u)
    np.testing.assert_allclose(0.5 * (ss + ps), c, atol=1e-14)
    np.testing.assert_allclose(ss - c, t[:, None] * 0.5 * n, atol=1e-14)
    np.testing.assert_allclose(ps - c, -t[:, None] * 0.5 * n, atol=1e-14)
    assert np.max(np.abs(np.sum((ss - c) * tangent, axis=1))) < 1e-14
    assert np.all(t[1:-1] > 0)
    np.testing.assert_array_equal(ss[[0, -1]], ps[[0, -1]])
    profile = generate_normal_offset_profile(spec, ref.native_id)
    decoded = NACA65SplineSpec.model_validate_json(spec.model_dump_json())
    rebuilt = generate_normal_offset_profile(decoded, ref.native_id)
    assert rebuilt.model_dump() == profile.model_dump()
    assert profile.leading_osculating_circle
    assert not profile.trailing_osculating_circle
    assert profile.edge_metrics["trailing_edge_radius_ratio"] == 0
    assert profile.metadata["trailing_edge_osculating_radius"] is None
    assert profile.edge_metrics["trailing_wedge_angle_deg"] > 0
    assert profile.edge_metrics["relative_curvature_knot_jump_max"] < 1e-4
    assert len(profile.metadata["edge_views"]["leading"]["suction"]) == 501
    for values in normal_offset_curvatures(spec, np.linspace(0, 1, 1201)):
        assert np.all(np.isfinite(values))
    # The old over-fitted nose had 3-4 spurious inflections in the first 2%.
    nose_u = np.linspace(0, np.sqrt(0.02), 1501) ** 2
    for values in normal_offset_curvatures(spec, nose_u):
        signs = np.sign(values[10:-10])
        assert np.count_nonzero(signs[1:] != signs[:-1]) <= 1
    # Avoid the artificial a=1 hook from a derivative sampled at 1-epsilon.
    for tail in evaluate_normal_offset(spec, np.linspace(0.98, 1, 1001))[:2]:
        delta = np.gradient(tail, axis=0)
        angle = np.unwrap(np.arctan2(delta[:, 1], delta[:, 0]))
        assert np.degrees(np.ptp(angle)) < 15
    # The LE limiting circle follows from geometry, not an assigned thickness.
    v = np.array([0, 1e-7, 2e-7])
    points = evaluate_normal_offset(spec, v * v)[0]
    d1, d2 = points[1] - points[0], points[2] - points[1]
    cross = abs(d1[0] * d2[1] - d1[1] * d2[0])
    radius = np.linalg.norm(d1) * np.linalg.norm(d2) * np.linalg.norm(d1 + d2) / (2 * cross)
    assert radius == pytest.approx(profile.edge_metrics["leading_edge_radius_ratio"], rel=0.003)


def test_curve_distance_has_no_sample_grid_floor():
    line = np.array([[0.0, 0.0], [1.0, 0.0]])
    samples = np.array([[0.1234, 0.0], [0.6873, 0.0], [0.5, 0.01]])
    np.testing.assert_allclose(point_to_polyline(samples, line), [0, 0, 0.01], atol=1e-15)


def test_sixseries_trailing_gap_is_not_misreported_as_a_rounding_radius():
    reference = generate_naca65(NACA65Spec(loading_parameter_a=1.0))
    assert reference.metadata["raw_trailing_edge_gap_ratio"] > 0
    spec, _ = fit_decomposition(reference)
    profile = generate_normal_offset_profile(spec, reference.native_id)
    assert profile.metadata["trailing_edge_kind"].startswith("sharp")
    assert profile.trailing_osculating_circle == []


def test_legacy_benchmark_is_never_rewritten(tmp_path):
    path = tmp_path / "benchmark_manifest.json"
    path.write_text(json.dumps({"benchmark_version": "naca65-benchmark-1.0.0"}))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="旧基准"):
        NACA65BenchmarkStore(tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / "catalog.duckdb").exists()
