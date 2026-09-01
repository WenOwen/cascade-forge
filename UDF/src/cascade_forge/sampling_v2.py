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
from .models_v2 import DatasetManifestV2, GeometryIntentV2, GeometrySpecV2
from .storage_v2 import DatasetStoreV2
from .validation_v2 import validate_profile_v2

INTENT_PARAMETER_ORDER = [
    "inlet_metal_angle_deg",
    "turning_angle_deg",
    "stagger_deg",
    "camber_le_tangent_ratio",
    "camber_te_tangent_ratio",
    "max_thickness_ratio",
    "max_thickness_x_ratio",
    "leading_edge_radius_ratio",
    "trailing_edge_radius_ratio",
    "fore_shape_factor",
    "aft_shape_factor",
]


def load_dataset_config_v2(path: str | Path) -> dict[str, Any]:
    config = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if str(config.get("schema_version")) != "2.0.0":
        raise ValueError("v2 生成器只接受 schema_version 2.0.0 配置")
    if str(config.get("generator_version")) != "unified-bspline-g2-v2":
        raise ValueError("配置不是统一 B-spline/G2 v2 内核")
    missing = [name for name in INTENT_PARAMETER_ORDER if name not in config.get("parameters", {})]
    if missing:
        raise ValueError(f"v2 配置缺少参数范围: {', '.join(missing)}")
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
    if not store.anchor_gate_passed():
        raise RuntimeError("NASA 48 锚点拟合门禁尚未通过，禁止生成正式 v2 数据集")

    state_path = store.root / "generation_state.json"
    state = _load_state(state_path, store)
    accepted = store.count("generated")
    proposal_cursor = int(state["next_proposal_index"])
    rejected = store.rejected_count()
    existing_ids = store.geometry_ids()
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
    executor = ProcessPoolExecutor(max_workers=worker_count) if worker_count > 1 else None
    try:
        while accepted < target:
            candidate_count = min(max(1, worker_count), target - accepted + worker_count)
            proposals = sobol_intents_v2(config, start=proposal_cursor, count=candidate_count)
            payloads = [
                {
                    "proposal_index": proposal_cursor + index,
                    "intent": _jsonable(parameters),
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
                    geometry, features, report = result["row"]
                    if geometry.geometry_id not in existing_ids and accepted < target:
                        pending_rows.append(
                            (
                                geometry,
                                features,
                                report,
                                {"proposal_index": result["proposal_index"]},
                            )
                        )
                        existing_ids.add(geometry.geometry_id)
                        accepted += 1
                else:
                    pending_rejected.append(result["rejected"])
                    rejected += 1
                if len(pending_rows) >= batch_size or len(pending_rejected) >= batch_size:
                    _flush(store, pending_rows, pending_rejected, accepted, proposal_cursor)
                    _write_state(state_path, accepted, proposal_cursor)
                if progress:
                    progress(
                        {
                            "proposals": proposal_cursor,
                            "accepted": accepted,
                            "rejected": rejected,
                        }
                    )
                if accepted >= target:
                    break
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    _flush(store, pending_rows, pending_rejected, accepted, proposal_cursor)
    manifest = DatasetManifestV2(
        seed=int(config["seed"]),
        target_valid=target,
        generated_valid=store.count("generated"),
        anchors=store.anchor_count(),
        anchor_fits=store.fit_count(),
        anchor_gate_passed=store.anchor_gate_passed(),
        proposals=proposal_cursor,
        rejected_proposals=store.rejected_count(),
        acceptance_rate=(store.count("generated") / proposal_cursor if proposal_cursor else 0.0),
        config_sha256=file_sha256(config_path),
        files=store.artifact_hashes(),
        batch_manifests=store.batch_manifests(),
        rejection_distribution=_rejection_distribution(store),
        provenance={
            "config": str(config_path),
            "mathematical_kernel": "unified-bspline-g2-constrained-normal-fit",
        },
    )
    store.write_manifest(manifest)
    _write_state(
        state_path,
        accepted,
        proposal_cursor,
        completed_at=datetime.now(UTC).isoformat(),
    )
    return manifest


def _evaluate_proposal(payload: dict[str, Any]) -> dict[str, Any]:
    proposal_index = int(payload["proposal_index"])
    try:
        intent = GeometryIntentV2.model_validate(payload["intent"])
        geometry = generate_profile(
            GeometrySpecV2(
                intent=intent,
                points_per_side=int(payload["points_per_side"]),
            )
        )
        features = extract_features(geometry)
        report = validate_profile_v2(geometry, features=features)
    except (ValueError, np.linalg.LinAlgError, FloatingPointError) as exc:
        return _rejected_result(
            proposal_index,
            payload["intent"],
            None,
            [{"code": "generation_error", "message": str(exc)}],
        )
    errors = [
        {"code": issue.code, "message": issue.message}
        for issue in report.issues
        if issue.severity == "error"
    ]
    if errors:
        return _rejected_result(
            proposal_index,
            payload["intent"],
            intent.intent_id,
            errors,
        )
    return {
        "accepted": True,
        "proposal_index": proposal_index,
        "row": (geometry, features, report),
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
        "rejected": {
            "proposal_index": proposal_index,
            "intent_id": intent_id,
            "parameters": parameters,
            "reasons": reasons,
        },
    }


def _flush(
    store: DatasetStoreV2,
    rows: list,
    rejected: list[dict[str, Any]],
    accepted: int,
    proposal_cursor: int,
) -> None:
    if rows:
        store.append_geometries(
            rows,
            batch_metadata={
                "accepted": accepted,
                "next_proposal_index": proposal_cursor,
            },
        )
        rows.clear()
    if rejected:
        store.append_rejected(rejected)
        rejected.clear()


def _load_state(path: Path, store: DatasetStoreV2) -> dict[str, int]:
    persisted = (
        json.loads(path.read_text(encoding="utf-8"))
        if path.exists()
        else {"next_proposal_index": 0}
    )
    import duckdb

    generated_next = 0
    rejected_next = 0
    generated_count = store.count("generated")
    rejected_count = store.rejected_count()
    with store._lock, duckdb.connect(str(store.catalog_path)) as connection:
        if generated_count:
            generated_next = int(
                connection.execute(
                    "SELECT coalesce(max(proposal_index), -1) + 1 "
                    "FROM geometries_v2 WHERE origin = 'generated'"
                ).fetchone()[0]
            )
        if rejected_count:
            rejected_next = int(
                connection.execute(
                    "SELECT coalesce(max(proposal_index), -1) + 1 FROM rejected_v2"
                ).fetchone()[0]
            )
    return {
        "accepted": store.count("generated"),
        "next_proposal_index": max(
            int(persisted.get("next_proposal_index", 0)),
            generated_next,
            rejected_next,
        ),
    }


def _write_state(path: Path, accepted: int, proposal_cursor: int, **extra: Any) -> None:
    payload = {
        "schema_version": "2.0.0",
        "generator_version": "unified-bspline-g2-v2",
        "accepted": accepted,
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
