from __future__ import annotations

import json
import math
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.stats import qmc

from .features import extract_features
from .geometry2d import generate_profile
from .models import DatasetManifest, GeometrySpec, Handedness, file_sha256
from .storage import DatasetStore
from .validation import validate_profile

PARAMETER_ORDER = [
    "inlet_metal_angle_deg",
    "turning_angle_deg",
    "quarter_angle_bias_deg",
    "three_quarter_angle_bias_deg",
    "max_thickness_ratio",
    "max_thickness_x_ratio",
    "leading_edge_radius_ratio",
    "trailing_edge_thickness_ratio",
    "trailing_edge_half_wedge_deg",
    "fore_shape_factor",
    "aft_shape_factor",
]


def load_dataset_config(path: str | Path) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def sobol_specs(config: dict[str, Any], proposal_count: int) -> list[dict[str, Any]]:
    exponent = math.ceil(math.log2(max(2, proposal_count)))
    unit = qmc.Sobol(d=len(PARAMETER_ORDER) + 1, scramble=True, seed=int(config["seed"]))
    samples = unit.random_base2(exponent)[:proposal_count]
    ranges = config["parameters"]
    proposals: list[dict[str, Any]] = []
    for sample in samples:
        values: dict[str, Any] = {}
        for index, name in enumerate(PARAMETER_ORDER):
            lower, upper = map(float, ranges[name])
            values[name] = lower + float(sample[index]) * (upper - lower)
        values["handedness"] = Handedness.RIGHT if sample[-1] < 0.5 else Handedness.LEFT
        values["points_per_side"] = int(config.get("points_per_side", 201))
        proposals.append(values)
    return proposals


def generate_dataset(
    config_path: str | Path,
    output_root: str | Path,
    *,
    target_valid: int | None = None,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> DatasetManifest:
    config_path = Path(config_path).resolve()
    config = load_dataset_config(config_path)
    if str(config.get("schema_version")) == "2.0.0":
        from .sampling_v2 import generate_dataset_v2

        return generate_dataset_v2(
            config_path,
            output_root,
            target_valid=target_valid,
            progress=progress,
        )
    target = int(target_valid or config["target_valid"])
    store = DatasetStore(output_root)
    existing_ids = store.geometry_ids()
    generated_existing = store.count("generated")
    needed = max(0, target - generated_existing)
    proposal_count = max(1024, 2 ** math.ceil(math.log2(max(needed * 3, 2))))
    proposals = sobol_specs(config, proposal_count)
    batch_size = int(config.get("batch_size", 512))
    accepted_batch = []
    rejected_batch: list[dict[str, Any]] = []
    accepted = generated_existing
    rejected = store.rejected_count()

    for index, parameters in enumerate(proposals):
        if accepted >= target:
            break
        try:
            spec = GeometrySpec(**parameters)
            if spec.geometry_id in existing_ids:
                continue
            geometry = generate_profile(spec)
            report = validate_profile(geometry)
            if report.valid:
                features = extract_features(geometry)
                accepted_batch.append((geometry, features, report))
                existing_ids.add(spec.geometry_id)
                accepted += 1
            else:
                rejected_batch.append(
                    {
                        "proposal_index": index,
                        "geometry_id": spec.geometry_id,
                        "parameters": spec.canonical_payload(),
                        "reasons": [issue.model_dump() for issue in report.issues],
                    }
                )
                rejected += 1
        except (ValueError, np.linalg.LinAlgError) as exc:
            rejected_batch.append(
                {
                    "proposal_index": index,
                    "parameters": _jsonable(parameters),
                    "reasons": [{"code": "generation_error", "message": str(exc)}],
                }
            )
            rejected += 1

        if len(accepted_batch) >= batch_size:
            store.append_geometries(accepted_batch)
            accepted_batch.clear()
        if len(rejected_batch) >= batch_size:
            store.append_rejected(rejected_batch)
            rejected_batch.clear()
        if progress and index % 100 == 0:
            progress({"proposals": index + 1, "accepted": accepted, "rejected": rejected})

    store.append_geometries(accepted_batch)
    store.append_rejected(rejected_batch)
    if accepted < target:
        raise RuntimeError(f"候选不足：只得到 {accepted}/{target} 个有效叶型")

    manifest = DatasetManifest(
        dataset_name=config["dataset_name"],
        schema_version=str(config["schema_version"]),
        generator_version=str(config["generator_version"]),
        seed=int(config["seed"]),
        target_valid=target,
        generated_valid=store.count("generated"),
        anchors=store.count("anchor"),
        rejected=store.rejected_count(),
        config_sha256=file_sha256(config_path),
        created_at=datetime.now(UTC).isoformat(),
        files=store.artifact_hashes(),
        provenance={
            "config_path": str(config_path),
            "command": "cascade-forge generate",
            "source": "deterministic Sobol geometry generation",
        },
    )
    store.write_manifest(manifest)
    return manifest


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda item: getattr(item, "value", str(item))))
