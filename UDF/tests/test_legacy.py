from __future__ import annotations

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

