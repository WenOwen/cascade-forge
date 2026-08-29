from __future__ import annotations

import json
from pathlib import Path

from cascade_forge.legacy import ingest_legacy_anchors
from cascade_forge.storage import DatasetStore


def test_curated_nasa_anchor_import_is_complete_and_idempotent(tmp_path: Path) -> None:
    source = Path("data/raw/nasa120859")
    first = ingest_legacy_anchors(
        source,
        tmp_path / "dataset",
        raw_destination=tmp_path / "copied-raw",
    )
    second = ingest_legacy_anchors(
        source,
        tmp_path / "dataset",
        raw_destination=tmp_path / "copied-raw",
    )
    store = DatasetStore(tmp_path / "dataset")
    assert first["count"] == second["count"] == 48
    assert store.count("anchor") == 48
    r1e = next(
        entry
        for entry in first["entries"]
        if entry["blade_row"] == "R1" and entry["section"] == "e"
    )
    assert r1e["validation_status"] == "warning"
    assert any("可疑尾缘半径" in warning for warning in r1e["warnings"])


def test_in_place_anchor_ingest_preserves_first_source_manifest(tmp_path: Path) -> None:
    source = tmp_path / "raw"
    ingest_legacy_anchors(
        Path("data/raw/nasa120859"),
        tmp_path / "first-dataset",
        raw_destination=source,
    )
    manifest_path = source / "source_manifest.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    ingest_legacy_anchors(source, tmp_path / "second-dataset", raw_destination=source)
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == original
