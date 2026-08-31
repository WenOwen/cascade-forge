from __future__ import annotations

from pathlib import Path

from cascade_forge.fitting_v2 import (
    _refresh_manifest_if_present,
    fit_anchor_geometry,
    load_legacy_anchor_geometries,
)
from cascade_forge.models_v2 import CurveFamily, DatasetManifestV2
from cascade_forge.storage_v2 import DatasetStoreV2

RAW_ROOT = Path(__file__).parents[1] / "data" / "raw" / "nasa120859"


def test_loads_exactly_48_read_only_anchors() -> None:
    anchors = load_legacy_anchor_geometries(RAW_ROOT)
    assert len(anchors) == 48
    assert len({anchor.geometry_id for anchor in anchors}) == 48
    r1e = next(anchor for anchor in anchors if anchor.source_id == "NASA120859/R1/e")
    assert any("可疑尾缘半径" in item for item in r1e.metadata["source_warnings"])


def test_anchor_fit_is_traceable_and_keeps_fit_even_when_rejected() -> None:
    anchor = load_legacy_anchor_geometries(RAW_ROOT)[0]
    record, geometry = fit_anchor_geometry(anchor, CurveFamily.BEZIER, CurveFamily.BSPLINE)
    assert record.source_anchor_id == anchor.geometry_id
    assert geometry.source_id == anchor.geometry_id
    assert geometry.origin == "anchor_fit"
    assert record.geometry_id == geometry.geometry_id
    assert record.fit_status in {"valid", "warning", "rejected"}
    assert record.rms_distance_ratio >= 0.0
    assert record.max_distance_ratio >= record.rms_distance_ratio


def test_fit_refreshes_existing_dataset_manifest(tmp_path) -> None:
    store = DatasetStoreV2(tmp_path)
    store.write_manifest(
        DatasetManifestV2(
            target_valid=1,
            generated_valid=1,
            complete_intent_groups=0,
            extra_records=1,
            proposals=1,
            rejected_proposals=0,
            acceptance_rate=1.0,
            config_sha256="0" * 64,
        )
    )
    _refresh_manifest_if_present(store)
    refreshed = DatasetManifestV2.model_validate_json(
        (tmp_path / "dataset_manifest.json").read_text(encoding="utf-8")
    )
    assert refreshed.anchors == 0
    assert refreshed.anchor_fits == 0
    assert refreshed.files == store.artifact_hashes()
