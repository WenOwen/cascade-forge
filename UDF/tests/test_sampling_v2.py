from __future__ import annotations

from pathlib import Path

from cascade_forge.audit_v2 import audit_dataset_v2
from cascade_forge.sampling_v2 import (
    _quota,
    generate_dataset_v2,
    load_dataset_config_v2,
    sobol_intents_v2,
)
from cascade_forge.storage_v2 import DatasetStoreV2

CONFIG = Path(__file__).parents[1] / "configs" / "geometry-v2.0.0.yaml"


def test_v2_quota_is_2222_paired_groups_plus_two_fixed_extras() -> None:
    config = load_dataset_config_v2(CONFIG)
    groups, extras = _quota(config, 20_000)
    assert groups == 2_222
    assert extras == [("bezier", "bspline"), ("nurbs", "nurbs")]


def test_sobol_resume_slice_is_deterministic() -> None:
    config = load_dataset_config_v2(CONFIG)
    complete = sobol_intents_v2(config, start=0, count=8)
    resumed = sobol_intents_v2(config, start=3, count=5)
    assert complete[3:] == resumed


def test_small_v2_dataset_is_exact_and_resumable(tmp_path) -> None:
    output = tmp_path / "geometry-v2"
    manifest = generate_dataset_v2(CONFIG, output, target_valid=2, workers=1)
    assert manifest.generated_valid == 2
    assert manifest.complete_intent_groups == 0
    assert manifest.extra_records == 2
    assert DatasetStoreV2(output).count("generated") == 2
    repeated = generate_dataset_v2(CONFIG, output, target_valid=2, workers=1)
    assert repeated.generated_valid == 2
    audit = audit_dataset_v2(output, rebuild_sample=2, require_anchors=False)
    assert audit["valid"], audit
