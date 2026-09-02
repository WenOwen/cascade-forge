from __future__ import annotations

import numpy as np
import pytest

from cascade_forge.curves import CurveEvaluator, angle_between_lines, signed_curvature
from cascade_forge.geometry2d import generate_profile
from cascade_forge.geometry_v2 import resolve_geometry_spec
from cascade_forge.models import Handedness
from cascade_forge.models_v2 import (
    GeometryIntentV2,
    GeometrySpecV2,
    MetalAngleReference,
    axial_to_tangential_angle,
    tangential_to_axial_angle,
)
from cascade_forge.validation import validate_profile


@pytest.fixture(scope="module")
def intent() -> GeometryIntentV2:
    return GeometryIntentV2(
        inlet_metal_angle_deg=55.0,
        outlet_metal_angle_deg=30.0,
        stagger_deg=42.0,
        max_thickness_ratio=0.10,
        max_thickness_x_ratio=0.42,
        leading_edge_radius_ratio=0.008,
        trailing_edge_radius_ratio=0.002,
    )


@pytest.mark.parametrize("handedness", [Handedness.RIGHT, Handedness.LEFT])
def test_unified_bspline_g2_contract(
    intent: GeometryIntentV2, handedness: Handedness
) -> None:
    geometry = generate_profile(
        GeometrySpecV2(intent=intent.model_copy(update={"handedness": handedness}))
    )
    suction, pressure, camber, thickness = geometry.arrays()
    assert suction.shape == pressure.shape == camber.shape == (201, 2)
    assert thickness.shape == (201,)
    assert len(geometry.suction_surface_curve.control_points) == 20
    assert len(geometry.pressure_surface_curve.control_points) == 20
    np.testing.assert_allclose(suction[[0, -1]], pressure[[0, -1]], atol=1e-12)
    assert np.min(thickness[1:-1]) > 0.0
    assert geometry.edge_metrics["normal_fit_rms"] <= 2e-4
    assert geometry.edge_metrics["normal_fit_max"] <= 1e-3
    assert geometry.edge_metrics["leading_tangency_error_deg"] <= 0.05
    assert geometry.edge_metrics["trailing_tangency_error_deg"] <= 0.05
    assert abs(geometry.edge_metrics["leading_edge_radius_ratio"] - 0.008) <= 1e-8
    assert abs(geometry.edge_metrics["trailing_edge_radius_ratio"] - 0.002) <= 1e-8
    assert geometry.edge_metrics["osculating_center_error"] <= 1e-8

    report = validate_profile(geometry)
    assert report.valid, [issue.model_dump() for issue in report.issues]
    assert report.rule_version == "geometry-rules-v2-g2"

    for circle_name, center_name, target_radius in (
        ("leading_osculating_circle", "leading_edge_center", 0.008),
        ("trailing_osculating_circle", "trailing_edge_center", 0.002),
    ):
        circle = np.asarray(getattr(geometry, circle_name))
        center = np.asarray(geometry.metadata[center_name])
        radii = np.linalg.norm(circle - center, axis=1)
        np.testing.assert_allclose(radii, target_radius, atol=1e-12)


def test_endpoint_g2_shares_tangent_and_osculating_radius(
    intent: GeometryIntentV2,
) -> None:
    geometry = generate_profile(GeometrySpecV2(intent=intent))
    suction = CurveEvaluator(geometry.suction_surface_curve)
    pressure = CurveEvaluator(geometry.pressure_surface_curve)
    for parameter, radius in ((0.0, 0.008), (1.0, 0.002)):
        suction_tangent = suction.evaluate([parameter], 1)[0]
        pressure_tangent = pressure.evaluate([parameter], 1)[0]
        assert angle_between_lines(suction_tangent, pressure_tangent) <= 0.05
        suction_radius = 1.0 / abs(signed_curvature(suction, [parameter])[0])
        pressure_radius = 1.0 / abs(signed_curvature(pressure, [parameter])[0])
        np.testing.assert_allclose([suction_radius, pressure_radius], radius, atol=1e-10)


def test_geometry_id_uses_resolved_contract_not_resolution_diagnostics(
    intent: GeometryIntentV2,
) -> None:
    resolved = resolve_geometry_spec(GeometrySpecV2(intent=intent))
    changed = resolved.model_copy(update={"resolution_residuals": {"diagnostic": 99.0}})
    assert resolved.geometry_id == changed.geometry_id


def test_v2_generation_is_deterministic(intent: GeometryIntentV2) -> None:
    spec = GeometrySpecV2(intent=intent)
    first = generate_profile(spec)
    second = generate_profile(spec)
    assert first.geometry_id == second.geometry_id
    np.testing.assert_array_equal(first.suction, second.suction)
    np.testing.assert_array_equal(first.pressure, second.pressure)


def test_axial_tangential_reference_round_trip_and_identity(intent: GeometryIntentV2) -> None:
    assert tangential_to_axial_angle(axial_to_tangential_angle(-4.65)) == pytest.approx(-4.65)
    axial = generate_profile(GeometrySpecV2(intent=intent))
    tangential_intent = intent.model_copy(
        update={
            "metal_angle_reference": MetalAngleReference.TANGENTIAL,
            "inlet_metal_angle_deg": intent.inlet_metal_angle_tangential_deg,
            "outlet_metal_angle_deg": intent.outlet_metal_angle_tangential_deg,
        }
    )
    tangential = generate_profile(GeometrySpecV2(intent=tangential_intent))
    assert tangential.geometry_id == axial.geometry_id
    assert tangential.intent_id == axial.intent_id
    np.testing.assert_allclose(tangential.suction, axial.suction, atol=1e-13)


def test_stagger_with_synchronized_metal_angles_is_rigid_placement(
    intent: GeometryIntentV2,
) -> None:
    baseline = generate_profile(GeometrySpecV2(intent=intent))
    delta = 11.0
    moved_intent = intent.model_copy(
        update={
            "stagger_deg": intent.stagger_deg + delta,
            "inlet_metal_angle_deg": intent.inlet_metal_angle_deg + delta,
            "outlet_metal_angle_deg": intent.outlet_metal_angle_deg + delta,
        }
    )
    moved = generate_profile(GeometrySpecV2(intent=moved_intent))
    assert moved.geometry_id == baseline.geometry_id
    assert moved.intent_id == baseline.intent_id
    np.testing.assert_allclose(moved.suction, baseline.suction, atol=1e-13)
    assert moved.realized_stagger_deg == pytest.approx(
        baseline.realized_stagger_deg + delta
    )
