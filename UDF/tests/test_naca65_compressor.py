from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from cascade_forge.benchmark_naca65 import benchmark_specs, run_naca65_benchmark
from cascade_forge.fitting_naca65 import fit_reference_geometry
from cascade_forge.models_naca65 import NACA65BenchmarkConfig, NACA65Spec, NACA65SplineSpec
from cascade_forge.naca65 import generate_naca65
from cascade_forge.normal_bspline import generate_normal_offset_profile
from cascade_forge.web import create_app


def test_published_blower_ordinates_and_separate_legacy_identity():
    ref = generate_naca65("65-010")
    c = np.asarray(ref.construction_camber)
    half = np.asarray(ref.construction_half_thickness)
    # NACA TR 1368 Table I (percent chord), including the finite TE ordinate.
    for x, y in [(0.005, 0.00752), (0.4, 0.05057), (0.95, 0.00354), (1.0, 0.0015)]:
        assert half[np.argmin(abs(c[:, 0] - x))] == pytest.approx(y, abs=1e-12)
    assert ref.metadata["raw_trailing_edge_gap_ratio"] == pytest.approx(0.003)
    assert (
        ref.spec.native_id != NACA65Spec(design_lift_coefficient=0, loading_parameter_a=1).native_id
    )
    with pytest.raises(ValidationError, match="固定为 a=1.0"):
        NACA65Spec(family="65-compressor", loading_parameter_a=0.8)


@pytest.mark.parametrize(
    "spec",
    benchmark_specs(NACA65BenchmarkConfig(compressor=True))
    + [
        NACA65Spec(family="65-compressor", thickness_ratio=t, design_lift_coefficient=2)
        for t in (0.03, 0.18)
    ],
)
def test_compressor_round_cap_fit_and_reconstruction(spec):
    reference = generate_naca65(spec)
    report, fitted = fit_reference_geometry(reference)
    assert report.fit_status == "valid", report.model_dump()
    assert report.diagnostics["outline_self_intersects"] == 0
    radius = 0.0015 * spec.thickness_ratio / 0.1
    arc = np.asarray(fitted.metadata["trailing_edge_arc"])
    center = np.asarray(fitted.metadata["trailing_edge_center"])
    np.testing.assert_allclose(np.linalg.norm(arc - center, axis=1), radius, atol=1e-14)
    # Geometric tangent approaches at the contacts are perpendicular to radius.
    for view_name, contact in [("suction", arc[0]), ("pressure", arc[-1])]:
        side = np.asarray(fitted.metadata["edge_views"]["trailing"][view_name])
        i = np.argmin(np.linalg.norm(side - contact, axis=1))
        tangent = side[i] - side[i - 1]
        assert abs(np.dot(tangent / np.linalg.norm(tangent), (contact - center) / radius)) < 0.002
    np.testing.assert_array_equal(
        np.asarray(fitted.suction)[[0, -1]], np.asarray(fitted.pressure)[[0, -1]]
    )
    rebuilt = generate_normal_offset_profile(
        NACA65SplineSpec.model_validate_json(fitted.spec.model_dump_json()), reference.native_id
    )
    assert rebuilt.model_dump() == fitted.model_dump()


def test_compressor_http_export_and_benchmark_resume(tmp_path):
    root = tmp_path / "compressor"
    config = NACA65BenchmarkConfig(
        compressor=True, thickness_ratios=(0.1,), design_lift_coefficients=(0.8,)
    )
    first = run_naca65_benchmark(config, root)
    resumed = run_naca65_benchmark(config, root)
    assert first.total_cases == resumed.completed_cases == 1
    assert first.run_id == resumed.run_id
    assert "TR 1368" in first.source_provenance["source"]
    with TestClient(create_app(tmp_path / "v1", naca65_dataset_root=root)) as client:
        response = client.post("/api/v2/naca65/fit", json={"spec": {"family": "65-compressor"}})
        assert response.status_code == 200, response.text
        assert (
            response.json()["fitted_geometry"]["edge_metrics"]["trailing_edge_radius_ratio"]
            == 0.0015
        )
        fit_id = client.get("/api/v2/naca65/cases").json()["items"][0]["fit_id"]
        exported = client.get(f"/api/v2/naca65/cases/{fit_id}/export?format=json").json()
        assert exported["reference"]["spec"]["family"] == "65-compressor"
        for format in ("csv", "parquet"):
            response = client.get(f"/api/v2/naca65/cases/{fit_id}/export?format={format}")
            assert response.status_code == 200 and response.content
