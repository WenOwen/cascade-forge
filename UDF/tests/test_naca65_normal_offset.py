from __future__ import annotations

import json

import numpy as np
import pytest

from cascade_forge.benchmark_naca65 import benchmark_specs
from cascade_forge.curves import CurveEvaluator, unit_tangent_and_normal
from cascade_forge.fitting_naca65 import point_to_polyline
from cascade_forge.models_naca65 import NACA65BenchmarkConfig, NACA65SplineSpec
from cascade_forge.naca65 import generate_naca65
from cascade_forge.normal_bspline import (
    evaluate_normal_offset,
    fit_decomposition,
    generate_normal_offset_profile,
)
from cascade_forge.storage_naca65 import NACA65BenchmarkStore


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
    assert len(spec.camber_curve.control_points) > 6
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


def test_legacy_benchmark_is_never_rewritten(tmp_path):
    path = tmp_path / "benchmark_manifest.json"
    path.write_text(json.dumps({"benchmark_version": "naca65-benchmark-1.0.0"}))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="旧基准"):
        NACA65BenchmarkStore(tmp_path)
    assert path.read_bytes() == before
    assert not (tmp_path / "catalog.duckdb").exists()
