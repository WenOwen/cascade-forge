from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from cascade_forge.models_naca65 import NACA65Family, NACA65InputMode, NACA65Spec
from cascade_forge.naca65 import generate_naca65, parse_naca65_designation


@pytest.mark.parametrize(
    ("family", "thickness", "lift", "a", "expected"),
    [
        (
            "65",
            0.10,
            0.8,
            0.8,
            [
                [0.0, 0.0, 0.0, 0.0],
                [-0.0003931057, 0.0018225129, 0.0008931058, -0.0014868507],
                [0.0089312578, 0.0159794465, 0.0160687286, -0.0061268643],
                [0.2460182607, 0.0877751783, 0.2539817989, -0.0018993294],
                [0.7525312304, 0.0698168725, 0.7474679351, 0.0184062645],
                [0.9999993443, 0.0000002722, 0.9999993443, -0.0000000601],
            ],
        ),
        (
            "65A",
            0.10,
            0.8,
            0.8,
            [
                [0.0, 0.0, 0.0, 0.0],
                [-0.0003816477, 0.0018236359, 0.0008816477, -0.0014948613],
                [0.0089055849, 0.0161911026, 0.0160944015, -0.0065406947],
                [0.2461156249, 0.0867140740, 0.2538844347, -0.0026003965],
                [0.7527944446, 0.0721789375, 0.7472047210, 0.0142338648],
                [1.0000296831, -0.0000092745, 0.9999690056, 0.0000022473],
            ],
        ),
        (
            "65",
            0.06,
            0.0,
            0.6,
            [
                [0.0, 0.0, 0.0, 0.0],
                [0.00025, 0.0010644066, 0.00025, -0.0010644066],
                [0.0124999927, 0.0070525780, 0.0124999927, -0.0070525780],
                [0.2500000298, 0.0269601401, 0.2500000298, -0.0269601401],
                [0.7499995828, 0.0159266498, 0.7499995828, -0.0159266498],
                [0.9999993443, 0.0000000989, 0.9999993443, -0.0000000989],
            ],
        ),
    ],
)
def test_native_generator_matches_pinned_openvsp_sixseries(
    family: str,
    thickness: float,
    lift: float,
    a: float,
    expected: list[list[float]],
) -> None:
    geometry = generate_naca65(
        NACA65Spec(
            family=NACA65Family(family),
            thickness_ratio=thickness,
            design_lift_coefficient=lift,
            loading_parameter_a=a,
        )
    )
    suction = np.asarray(geometry.raw_suction)
    pressure = np.asarray(geometry.raw_pressure)
    indices = [0, 1, 50, 100, 150, 175]
    actual = np.column_stack([suction[indices], pressure[indices]])
    assert np.max(np.abs(actual - np.asarray(expected))) <= 3e-6


def test_compressor_designation_maps_to_general_parameters() -> None:
    preset = parse_naca65_designation("NACA 65-(12)10")
    general = NACA65Spec(
        thickness_ratio=0.10,
        design_lift_coefficient=1.2,
        loading_parameter_a=1.0,
    )
    assert preset.input_mode == NACA65InputMode.COMPRESSOR_PRESET
    assert preset.native_id == general.native_id
    assert generate_naca65(preset).raw_suction == generate_naca65(general).raw_suction


def test_65a_requires_fixed_loading_parameter() -> None:
    with pytest.raises(ValidationError, match="固定为 0.8"):
        NACA65Spec(family="65A", loading_parameter_a=1.0)


def test_normalized_surfaces_share_endpoints_and_resolution() -> None:
    geometry = generate_naca65(NACA65Spec())
    suction = np.asarray(geometry.normalized_suction)
    pressure = np.asarray(geometry.normalized_pressure)
    assert suction.shape == pressure.shape == (201, 2)
    assert np.allclose(suction[[0, -1]], pressure[[0, -1]], atol=1e-14)
    assert np.isclose(np.max(geometry.thickness), 0.10, atol=5e-4)
