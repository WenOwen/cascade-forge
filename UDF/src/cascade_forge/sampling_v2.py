from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml
from scipy.stats import qmc

from .features import extract_features
from .geometry2d import generate_profile
from .models import Handedness, file_sha256
from .models_v2 import (
    CurveFamily,
    DatasetManifestV2,
    GeometryIntentV2,
    GeometrySpecV2,
)
from .storage_v2 import DatasetStoreV2
from .validation_v2 import validate_profile_v2

INTENT_PARAMETER_ORDER = [
    "inlet_metal_angle_deg",
    "turning_angle_deg",
    "quarter_angle_bias_deg",
    "three_quarter_angle_bias_deg",
    "max_thickness_ratio",
    "max_thickness_x_ratio",
    "leading_edge_radius_ratio",
    "trailing_edge_radius_ratio",
    "trailing_edge_half_wedge_deg",
    "fore_shape_factor",
    "aft_shape_factor",
]


def load_dataset_config_v2(path: str | Path) -> dict[str, Any]:
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if str(config.get("schema_version")) != "2.0.0":
        raise ValueError("v2 生成器只接受 schema_version 2.0.0 配置")
    return config


def sobol_intents_v2(config: dict[str, Any], *, start: int, count: int) -> list[dict[str, Any]]:
    sampler = qmc.Sobol(
        d=len(INTENT_PARAMETER_ORDER) + 1,
        scramble=True,
        seed=int(config["seed"]),
    )
    if start:
        sampler.fast_forward(start)
    samples = sampler.random(count)
    ranges = config["parameters"]
    proposals: list[dict[str, Any]] = []
    for sample in samples:
        values = {
            name: float(ranges[name][0])
            + float(sample[index]) * (float(ranges[name][1]) - float(ranges[name][0]))
            for index, name in enumerate(INTENT_PARAMETER_ORDER)
        }
        values["handedness"] = Handedness.RIGHT if sample[-1] < 0.5 else Handedness.LEFT
        proposals.append(values)
    return proposals


def generate_dataset_v2(
    config_path: str | Path,
    output_root: str | Path,
    *,
    target_valid: int | None = None,
    workers: int | None = None,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> DatasetManifestV2:
    config_path = Path(config_path).resolve()
    config = load_dataset_config_v2(config_path)
    target = int(target_valid or config["target_valid"])
    if target < 1:
        raise ValueError("target_valid 必须为正整数")
    store = DatasetStoreV2(output_root)
    state_path = store.root / "generation_state.json"
    state = _load_state(state_path, store)
    full_group_target, extra_combinations = _quota(config, target)
    accepted_groups = min(int(state["accepted_groups"]), full_group_target)
    accepted_extras = min(int(state["accepted_extras"]), len(extra_combinations))
    proposal_cursor = int(state["next_proposal_index"])
    existing_ids = store.geometry_ids()
    rejected = store.rejected_count()
    batch_size = int(config.get("batch_size", 256))
    pending_rows = []
    pending_rejected: list[dict[str, Any]] = []
    worker_count = max(
        1,
        int(
            workers
            if workers is not None
            else config.get("workers", min(8, max(1, (os.cpu_count() or 2) - 1)))
        ),
    )
    family_grid = [
        (camber.value, thickness.value) for camber in CurveFamily for thickness in CurveFamily
    ]

    executor = ProcessPoolExecutor(max_workers=worker_count) if worker_count > 1 else None
    try:
        while accepted_groups < full_group_target:
            candidate_count = max(1, worker_count)
            proposals = sobol_intents_v2(config, start=proposal_cursor, count=candidate_count)
            payloads = [
                {
                    "proposal_index": proposal_cursor + index,
                    "intent": _jsonable(parameters),
                    "combinations": family_grid,
                    "points_per_side": int(config.get("points_per_side", 201)),
                }
                for index, parameters in enumerate(proposals)
            ]
            results = (
                list(executor.map(_evaluate_proposal, payloads))
                if executor is not None
                else [_evaluate_proposal(payload) for payload in payloads]
            )
            for result in results:
                proposal_cursor = int(result["proposal_index"]) + 1
                if result["accepted"]:
                    for geometry, features, report in result["rows"]:
                        if geometry.geometry_id in existing_ids:
                            continue
                        pending_rows.append(
                            (
                                geometry,
                                features,
                                report,
                                {
                                    "intent_group_index": accepted_groups,
                                    "proposal_index": result["proposal_index"],
                                    "is_extra": False,
                                },
                            )
                        )
                        existing_ids.add(geometry.geometry_id)
                    accepted_groups += 1
                else:
                    pending_rejected.append(result["rejected"])
                    rejected += 1
                flushed = _flush_if_needed(
                    store,
                    pending_rows,
                    pending_rejected,
                    batch_size,
                    accepted_groups,
                    proposal_cursor,
                )
                if flushed:
                    _write_state(
                        state_path,
                        accepted_groups,
                        accepted_extras,
                        proposal_cursor,
                    )
                if progress:
                    progress(
                        {
                            "proposals": proposal_cursor,
                            "accepted_groups": accepted_groups,
                            "accepted": accepted_groups * 9 + accepted_extras,
                            "rejected": rejected,
                        }
                    )
                if accepted_groups >= full_group_target:
                    break

        while accepted_extras < len(extra_combinations):
            combination = extra_combinations[accepted_extras]
            proposal = sobol_intents_v2(config, start=proposal_cursor, count=1)[0]
            result = _evaluate_proposal(
                {
                    "proposal_index": proposal_cursor,
                    "intent": _jsonable(proposal),
                    "combinations": [combination],
                    "points_per_side": int(config.get("points_per_side", 201)),
                }
            )
            proposal_cursor += 1
            if result["accepted"]:
                geometry, features, report = result["rows"][0]
                if geometry.geometry_id not in existing_ids:
                    pending_rows.append(
                        (
                            geometry,
                            features,
                            report,
                            {
                                "intent_group_index": None,
                                "proposal_index": result["proposal_index"],
                                "is_extra": True,
                            },
                        )
                    )
                    existing_ids.add(geometry.geometry_id)
                    accepted_extras += 1
            else:
                pending_rejected.append(result["rejected"])
                rejected += 1
            flushed = _flush_if_needed(
                store,
                pending_rows,
                pending_rejected,
                batch_size,
                accepted_groups,
                proposal_cursor,
            )
            if flushed:
                _write_state(
                    state_path,
                    accepted_groups,
                    accepted_extras,
                    proposal_cursor,
                )
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)

    _flush(
        store,
        pending_rows,
        pending_rejected,
        accepted_groups,
        proposal_cursor,
    )
    generated = store.count("generated")
    if generated != target:
        raise RuntimeError(f"v2 配额不一致：期望 {target}，实际 {generated}")
    rejection_distribution = _rejection_distribution(store)
    accepted_proposals = full_group_target + len(extra_combinations)
    manifest = DatasetManifestV2(
        dataset_name=str(config["dataset_name"]),
        seed=int(config["seed"]),
        target_valid=target,
        generated_valid=generated,
        complete_intent_groups=full_group_target,
        extra_records=len(extra_combinations),
        proposals=proposal_cursor,
        rejected_proposals=store.rejected_count(),
        acceptance_rate=(accepted_proposals / proposal_cursor) if proposal_cursor else 1.0,
        config_sha256=file_sha256(config_path),
        files=store.artifact_hashes(),
        batch_manifests=store.batch_manifests(),
        rejection_distribution=rejection_distribution,
        provenance={
            "config_path": str(config_path),
            "command": "cascade-forge generate --dataset-version 2.0.0",
            "sampling": "scrambled Sobol paired-intent rejection sampling",
            "workers": str(worker_count),
        },
    )
    store.write_manifest(manifest)
    _write_state(
        state_path,
        accepted_groups,
        accepted_extras,
        proposal_cursor,
        completed_at=datetime.now(UTC).isoformat(),
    )
    return manifest


def _evaluate_proposal(payload: dict[str, Any]) -> dict[str, Any]:
    proposal_index = int(payload["proposal_index"])
    try:
        intent = GeometryIntentV2.model_validate(payload["intent"])
    except ValueError as exc:
        return _rejected_result(
            proposal_index,
            payload["intent"],
            None,
            [{"code": "intent_contract", "message": str(exc)}],
        )
    rows = []
    reasons = []
    for camber_name, thickness_name in payload["combinations"]:
        try:
            spec = GeometrySpecV2(
                intent=intent,
                camber_family=CurveFamily(camber_name),
                thickness_family=CurveFamily(thickness_name),
                points_per_side=int(payload["points_per_side"]),
            )
            geometry = generate_profile(spec)
            features = extract_features(geometry)
            report = validate_profile_v2(geometry, features=features)
            if not report.valid:
                reasons.extend(
                    {
                        "code": issue.code,
                        "message": issue.message,
                        "combination": f"{camber_name}x{thickness_name}",
                    }
                    for issue in report.issues
                    if issue.severity == "error"
                )
            rows.append((geometry, features, report))
        except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
            reasons.append(
                {
                    "code": "generation_error",
                    "message": str(exc),
                    "combination": f"{camber_name}x{thickness_name}",
                }
            )
    if reasons:
        return _rejected_result(
            proposal_index,
            payload["intent"],
            intent.intent_id,
            reasons,
        )
    return {
        "accepted": True,
        "proposal_index": proposal_index,
        "rows": rows,
    }


def _rejected_result(
    proposal_index: int,
    parameters: dict[str, Any],
    intent_id: str | None,
    reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "accepted": False,
        "proposal_index": proposal_index,
        "rows": [],
        "rejected": {
            "proposal_index": proposal_index,
            "intent_id": intent_id,
            "parameters": parameters,
            "reasons": reasons,
        },
    }


def _quota(config: dict[str, Any], target: int) -> tuple[int, list[tuple[str, str]]]:
    full_groups, remainder = divmod(target, 9)
    configured = [tuple(item) for item in config.get("extra_combinations", [])]
    fallback = [
        (camber.value, thickness.value) for camber in CurveFamily for thickness in CurveFamily
    ]
    combinations = (configured + fallback)[:remainder]
    return full_groups, combinations


def _flush_if_needed(
    store: DatasetStoreV2,
    rows: list,
    rejected: list[dict[str, Any]],
    batch_size: int,
    accepted_groups: int,
    proposal_cursor: int,
) -> bool:
    if len(rows) >= batch_size or len(rejected) >= batch_size:
        _flush(store, rows, rejected, accepted_groups, proposal_cursor)
        return True
    return False


def _flush(
    store: DatasetStoreV2,
    rows: list,
    rejected: list[dict[str, Any]],
    accepted_groups: int,
    proposal_cursor: int,
) -> None:
    if rows:
        store.append_geometries(
            rows,
            batch_metadata={
                "accepted_groups": accepted_groups,
                "next_proposal_index": proposal_cursor,
            },
        )
        rows.clear()
    if rejected:
        store.append_rejected(rejected)
        rejected.clear()


def _load_state(path: Path, store: DatasetStoreV2) -> dict[str, int]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    if store.count() == 0:
        return {
            "accepted_groups": 0,
            "accepted_extras": 0,
            "next_proposal_index": 0,
        }
    with store._lock:
        import duckdb

        with duckdb.connect(str(store.catalog_path)) as connection:
            groups, extras, next_proposal = connection.execute(
                """
                SELECT count(DISTINCT intent_group_index) FILTER (WHERE NOT is_extra),
                       count(*) FILTER (WHERE is_extra),
                       coalesce(max(proposal_index), -1) + 1
                FROM geometries_v2
                """
            ).fetchone()
    return {
        "accepted_groups": int(groups),
        "accepted_extras": int(extras),
        "next_proposal_index": int(next_proposal),
    }


def _write_state(
    path: Path,
    accepted_groups: int,
    accepted_extras: int,
    proposal_cursor: int,
    **extra: Any,
) -> None:
    payload = {
        "schema_version": "2.0.0",
        "accepted_groups": accepted_groups,
        "accepted_extras": accepted_extras,
        "next_proposal_index": proposal_cursor,
        **extra,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _rejection_distribution(store: DatasetStoreV2) -> dict[str, int]:
    if store.rejected_count() == 0:
        return {}
    import duckdb

    with store._lock, duckdb.connect(str(store.catalog_path)) as connection:
        rows = connection.execute("SELECT reasons_json FROM rejected_v2").fetchall()
    counts: Counter[str] = Counter()
    for (raw,) in rows:
        for reason in json.loads(raw):
            counts[str(reason.get("code", "unknown"))] += 1
    return dict(sorted(counts.items()))


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda item: getattr(item, "value", str(item))))
