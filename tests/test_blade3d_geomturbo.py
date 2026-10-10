from __future__ import annotations

from pathlib import Path

import numpy as np

from cascade_forge.blade3d import build_blade
from cascade_forge.geometry2d import generate_profile
from cascade_forge.geomturbo import export_blade_csv, export_geomturbo, parse_geomturbo
from cascade_forge.legacy import ingest_legacy_anchors, legacy_section_schedule
from cascade_forge.models import GeometrySpec, SectionDefinition, SectionSchedule
from cascade_forge.storage import DatasetStore


def _geometry(turning: float):
    return generate_profile(
        GeometrySpec(
            inlet_metal_angle_deg=58,
            turning_angle_deg=turning,
            quarter_angle_bias_deg=0,
            three_quarter_angle_bias_deg=0,
            max_thickness_ratio=0.09,
            max_thickness_x_ratio=0.4,
            leading_edge_radius_ratio=0.004,
            trailing_edge_thickness_ratio=0.006,
            trailing_edge_half_wedge_deg=8,
            fore_shape_factor=1,
            aft_shape_factor=1,
        )
    )


def test_blade_build_and_geomturbo_round_trip(tmp_path: Path) -> None:
    root = _geometry(28)
    tip = _geometry(36)
    geometries = {root.geometry_id: root, tip.geometry_id: tip}
    schedule = SectionSchedule(
        name="test-rotor",
        blade_count=28,
        output_section_count=5,
        sections=[
            SectionDefinition(
                span_fraction=0,
                radius_m=0.15,
                chord_m=0.08,
                geometry_id=root.geometry_id,
                stagger_deg=32,
            ),
            SectionDefinition(
                span_fraction=1,
                radius_m=0.3,
                chord_m=0.06,
                geometry_id=tip.geometry_id,
                stagger_deg=48,
                lean_m=0.002,
                sweep_m=0.003,
            ),
        ],
    )
    blade = build_blade(schedule, geometries)
    assert len(blade.sections) == 5
    assert blade.sections[2].interpolated
    path = export_geomturbo(blade, tmp_path / "blade.geomTurbo")
    parsed = parse_geomturbo(path)
    assert parsed["blade_count"] == 28
    assert len(parsed["suction"]) == 5
    original = np.asarray(blade.sections[2].suction_xyz)
    assert np.max(np.abs(parsed["suction"][2] - original)) < 1e-8
    csv_path = export_blade_csv(blade, tmp_path / "blade.csv")
    assert csv_path.exists()
    assert path.with_suffix(path.suffix + ".manifest.json").exists()


def test_nasa_r1_schedule_rebuilds_from_twelve_anchors(tmp_path: Path) -> None:
    source = Path("data/raw/nasa120859")
    dataset = tmp_path / "dataset"
    ingest_legacy_anchors(source, dataset, raw_destination=tmp_path / "raw")
    store = DatasetStore(dataset)
    schedule = legacy_section_schedule(source, "R1", output_section_count=14)
    blade = build_blade(schedule, store.get_geometry)
    assert len(schedule.sections) == 12
    assert len(blade.sections) == 14
    assert blade.schedule.blade_count == 28
    path = export_geomturbo(blade, tmp_path / "NASA120859-R1.geomTurbo")
    assert len(parse_geomturbo(path)["pressure"]) == 14
