from __future__ import annotations

from pathlib import Path

import yaml

from cascade_forge.features import extract_features
from cascade_forge.geometry2d import generate_profile
from cascade_forge.models import GeometrySpec
from cascade_forge.sampling import generate_dataset, sobol_specs
from cascade_forge.storage import DatasetStore
from cascade_forge.validation import validate_profile


def _spec() -> GeometrySpec:
    return GeometrySpec(
        inlet_metal_angle_deg=55,
        turning_angle_deg=30,
        quarter_angle_bias_deg=0,
        three_quarter_angle_bias_deg=0,
        max_thickness_ratio=0.09,
        max_thickness_x_ratio=0.4,
        leading_edge_radius_ratio=0.004,
        trailing_edge_thickness_ratio=0.007,
        trailing_edge_half_wedge_deg=8,
        fore_shape_factor=1,
        aft_shape_factor=1,
    )


def test_store_round_trip(tmp_path: Path) -> None:
    geometry = generate_profile(_spec())
    store = DatasetStore(tmp_path / "db")
    store.append_geometries([(geometry, extract_features(geometry), validate_profile(geometry))])
    loaded = store.get_geometry(geometry.geometry_id)
    assert loaded is not None
    assert loaded.geometry_id == geometry.geometry_id
    assert loaded.suction == geometry.suction
    assert store.count("generated") == 1


def test_sobol_is_reproducible() -> None:
    config = yaml.safe_load(
        Path("configs/geometry-v1.0.0.yaml").read_text(encoding="utf-8")
    )
    assert sobol_specs(config, 16) == sobol_specs(config, 16)


def test_small_dataset_generation(tmp_path: Path) -> None:
    config_path = Path("configs/geometry-v1.0.0.yaml")
    manifest = generate_dataset(config_path, tmp_path / "dataset", target_valid=12)
    assert manifest.generated_valid == 12
    store = DatasetStore(tmp_path / "dataset")
    assert len(store.geometry_ids()) == 12
    assert (tmp_path / "dataset" / "dataset_manifest.json").exists()

