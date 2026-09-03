from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .fitting_naca65 import fit_reference_geometry
from .models_naca65 import (
    NACA65BenchmarkConfig,
    NACA65BenchmarkManifest,
    NACA65Family,
    NACA65FitReport,
    NACA65Spec,
)
from .naca65 import SOURCE_PROVENANCE, generate_naca65
from .storage_naca65 import NACA65BenchmarkStore


def benchmark_specs(config: NACA65BenchmarkConfig) -> list[NACA65Spec]:
    specs = [
        NACA65Spec(
            family=NACA65Family.SERIES_65,
            thickness_ratio=thickness,
            design_lift_coefficient=lift,
            loading_parameter_a=a,
        )
        for thickness in config.thickness_ratios
        for lift in config.design_lift_coefficients
        for a in config.series_65_loading_parameters
    ]
    specs.extend(
        NACA65Spec(
            family=NACA65Family.SERIES_65A,
            thickness_ratio=thickness,
            design_lift_coefficient=lift,
            loading_parameter_a=config.series_65a_loading_parameter,
        )
        for thickness in config.thickness_ratios
        for lift in config.design_lift_coefficients
    )
    return specs


def run_naca65_benchmark(
    config: NACA65BenchmarkConfig,
    destination: str | Path,
    *,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> NACA65BenchmarkManifest:
    store = NACA65BenchmarkStore(destination)
    previous_manifest = store.latest_manifest()
    if previous_manifest is not None and previous_manifest.config != config:
        raise ValueError(
            "目标基准库已由不同的 NACA 65 拟合配置初始化；"
            "请使用相同配置恢复，或选择新的版本化目录"
        )
    specs = benchmark_specs(config)
    if len(specs) != config.case_count:
        raise RuntimeError("NACA 65 基准矩阵数量与配置不一致")
    existing = store.native_ids()
    for completed, spec in enumerate(specs, start=1):
        if spec.native_id not in existing:
            reference = generate_naca65(spec)
            try:
                report, fitted = fit_reference_geometry(
                    reference,
                    max_outer_iterations=config.max_outer_iterations,
                    max_design_evaluations=config.max_design_evaluations,
                )
            except Exception as exc:
                failed_key = json.dumps(
                    {"native_id": reference.native_id, "config": config.model_dump(mode="json")},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                report = NACA65FitReport(
                    fit_id="naca65_fit_failed_"
                    + hashlib.sha256(failed_key.encode()).hexdigest()[:24],
                    native_id=reference.native_id,
                    fit_status="failed",
                    rms_distance_ratio=None,
                    max_distance_ratio=None,
                    hausdorff_approx_ratio=None,
                    core_rms_distance_ratio=None,
                    core_max_distance_ratio=None,
                    suction_signed_normal_error=[],
                    pressure_signed_normal_error=[],
                    area_difference_ratio=None,
                    feature_deltas={},
                    iterations=0,
                    converged=False,
                    termination_reason=type(exc).__name__,
                    elapsed_seconds=0.0,
                    warnings=[str(exc)],
                )
                fitted = None
            store.append_case(reference, report, fitted)
            existing.add(spec.native_id)
        if progress:
            progress(
                {
                    "completed": completed,
                    "total": len(specs),
                    "stored": store.count(),
                }
            )

    now = datetime.now(UTC).isoformat()
    config_payload = config.model_dump(mode="json")
    run_key = json.dumps(
        {"config": config_payload, "source_commit": SOURCE_PROVENANCE["commit"]},
        sort_keys=True,
        separators=(",", ":"),
    )
    run_id = "naca65_run_" + hashlib.sha256(run_key.encode()).hexdigest()[:20]
    rows = store.list_cases(limit=1000)
    distribution = Counter(str(row["fit_status"]) for row in rows)
    manifest = NACA65BenchmarkManifest(
        run_id=run_id,
        config=config,
        total_cases=len(specs),
        completed_cases=len(existing.intersection({spec.native_id for spec in specs})),
        status_distribution=dict(distribution),
        source_provenance=SOURCE_PROVENANCE,
        files=store.artifact_hashes(),
        created_at=previous_manifest.created_at if previous_manifest else now,
        completed_at=now,
    )
    store.write_manifest(manifest)
    return manifest
