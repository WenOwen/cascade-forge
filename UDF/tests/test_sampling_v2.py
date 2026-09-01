from __future__ import annotations

from pathlib import Path

import pytest

from cascade_forge.audit_v2 import audit_dataset_v2
from cascade_forge.sampling_v2 import (
    generate_dataset_v2,
    load_dataset_config_v2,
    sobol_intents_v2,
)
from cascade_forge.storage_v2 import DatasetStoreV2

CONFIG = Path(__file__).parents[1] / "configs" / "geometry-v2.0.0.yaml"


def test_unified_v2_config_and_sobol_resume_are_deterministic() -> None:
    config = load_dataset_config_v2(CONFIG)
    assert config["generator_version"] == "unified-bspline-g2-v2"
    complete = sobol_intents_v2(config, start=0, count=8)
    resumed = sobol_intents_v2(config, start=3, count=5)
    assert complete[3:] == resumed


def test_dataset_generation_requires_anchor_gate(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="锚点拟合门禁"):
        generate_dataset_v2(CONFIG, tmp_path / "blocked", target_valid=1, workers=1)


def test_small_v2_dataset_is_exact_resumable_and_auditable(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(DatasetStoreV2, "anchor_gate_passed", lambda self: True)
    output = tmp_path / "geometry-v2"
    manifest = generate_dataset_v2(CONFIG, output, target_valid=2, workers=1)
    assert manifest.generated_valid == 2
    assert manifest.generator_version == "unified-bspline-g2-v2"
    assert DatasetStoreV2(output).count("generated") == 2
    repeated = generate_dataset_v2(CONFIG, output, target_valid=2, workers=1)
    assert repeated.generated_valid == 2
    audit = audit_dataset_v2(output, rebuild_sample=2, require_anchors=False)
    assert audit["valid"], audit
