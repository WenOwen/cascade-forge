from __future__ import annotations

import numpy as np
import pytest

from cascade_forge.features import extract_features
from cascade_forge.geometry2d import generate_profile
from cascade_forge.models import CascadeContext, GeometrySpec
from cascade_forge.validation import validate_profile


@pytest.fixture
def baseline_spec() -> GeometrySpec:
    return GeometrySpec(
        inlet_metal_angle_deg=58.0,
        turning_angle_deg=32.0,
        quarter_angle_bias_deg=2.0,
        three_quarter_angle_bias_deg=-1.0,
        max_thickness_ratio=0.10,
        max_thickness_x_ratio=0.40,
        leading_edge_radius_ratio=0.004,
        trailing_edge_thickness_ratio=0.008,
        trailing_edge_half_wedge_deg=8.0,
        fore_shape_factor=1.0,
        aft_shape_factor=1.0,
    )


def test_geometry_is_deterministic_and_closed(baseline_spec: GeometrySpec) -> None:
    first = generate_profile(baseline_spec)
    second = generate_profile(baseline_spec)
    assert first.geometry_id == second.geometry_id
    assert first.suction == second.suction
    suction, pressure, camber, thickness = first.arrays()
    assert np.linalg.norm(suction[0] - pressure[0]) < 1e-12
    assert abs(camber[-1, 1]) < 1e-12
    assert np.all(thickness[1:] > 0.0)


def test_target_features_and_validation(baseline_spec: GeometrySpec) -> None:
    geometry = generate_profile(baseline_spec)
    features = extract_features(geometry)
    assert features.inlet_metal_angle_deg == pytest.approx(58.0)
    assert features.outlet_metal_angle_deg == pytest.approx(26.0)
    assert features.max_thickness_ratio == pytest.approx(0.10, abs=5e-4)
    report = validate_profile(geometry, CascadeContext(solidity=1.3))
    assert report.valid, report.model_dump()


def test_geometry_id_changes_with_parameters(baseline_spec: GeometrySpec) -> None:
    changed = baseline_spec.model_copy(update={"max_thickness_ratio": 0.11})
    assert baseline_spec.geometry_id != changed.geometry_id


def test_invalid_outlet_angle_rejected() -> None:
    with pytest.raises(ValueError, match="出口金属角"):
        GeometrySpec(
            inlet_metal_angle_deg=20.0,
            turning_angle_deg=65.0,
            quarter_angle_bias_deg=0.0,
            three_quarter_angle_bias_deg=0.0,
            max_thickness_ratio=0.1,
            max_thickness_x_ratio=0.4,
            leading_edge_radius_ratio=0.004,
            trailing_edge_thickness_ratio=0.008,
            trailing_edge_half_wedge_deg=8.0,
            fore_shape_factor=1.0,
            aft_shape_factor=1.0,
        )
