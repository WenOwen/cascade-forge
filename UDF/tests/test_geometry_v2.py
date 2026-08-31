from __future__ import annotations

import itertools

import numpy as np
import pytest

from cascade_forge.geometry2d import generate_profile
from cascade_forge.models_v2 import CurveFamily, GeometryIntentV2, GeometrySpecV2


@pytest.fixture(scope="module")
def intent() -> GeometryIntentV2:
    return GeometryIntentV2(
        inlet_metal_angle_deg=55.0,
        turning_angle_deg=25.0,
        max_thickness_ratio=0.10,
        max_thickness_x_ratio=0.42,
        leading_edge_radius_ratio=0.008,
        trailing_edge_radius_ratio=0.002,
        trailing_edge_half_wedge_deg=8.0,
    )


@pytest.mark.parametrize(
    ("camber_family", "thickness_family"),
    itertools.product(list(CurveFamily), repeat=2),
)
def test_all_nine_curve_combinations(
    intent: GeometryIntentV2,
    camber_family: CurveFamily,
    thickness_family: CurveFamily,
) -> None:
    geometry = generate_profile(
        GeometrySpecV2(
            intent=intent,
            camber_family=camber_family,
            thickness_family=thickness_family,
        )
    )
    suction, pressure, camber, thickness = geometry.arrays()
    assert suction.shape == pressure.shape == camber.shape == (201, 2)
    assert thickness.shape == (201,)
    np.testing.assert_allclose(suction[0], pressure[0], atol=1e-12)
    np.testing.assert_allclose(suction[-1], pressure[-1], atol=1e-12)
    assert geometry.edge_metrics["normal_offset_error"] <= 1e-10
    assert geometry.edge_metrics["leading_tangency_error_deg"] <= 0.05
    assert geometry.edge_metrics["trailing_tangency_error_deg"] <= 0.05
    assert abs(geometry.edge_metrics["leading_edge_radius_ratio"] - 0.008) <= 1e-8
    assert abs(geometry.edge_metrics["trailing_edge_radius_ratio"] - 0.002) <= 1e-8

    for arc_name, center_name, target_radius in (
        ("leading_edge_arc", "leading_edge_center", 0.008),
        ("trailing_edge_arc", "trailing_edge_center", 0.002),
    ):
        arc = np.asarray(getattr(geometry, arc_name))
        center = np.asarray(geometry.metadata[center_name])
        radii = np.linalg.norm(arc - center, axis=1)
        np.testing.assert_allclose(radii, target_radius, atol=1e-8)


def test_geometry_id_uses_resolved_contract_not_basic_residual_metadata(
    intent: GeometryIntentV2,
) -> None:
    from cascade_forge.geometry_v2 import resolve_geometry_spec

    resolved = resolve_geometry_spec(GeometrySpecV2(intent=intent))
    changed = resolved.model_copy(update={"resolution_residuals": {"camber_rms": 99.0}})
    assert resolved.geometry_id == changed.geometry_id


def test_v2_generation_is_deterministic(intent: GeometryIntentV2) -> None:
    spec = GeometrySpecV2(
        intent=intent,
        camber_family=CurveFamily.NURBS,
        thickness_family=CurveFamily.NURBS,
    )
    first = generate_profile(spec)
    second = generate_profile(spec)
    assert first.geometry_id == second.geometry_id
    np.testing.assert_array_equal(first.suction, second.suction)
