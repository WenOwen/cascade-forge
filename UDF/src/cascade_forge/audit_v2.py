from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from .geometry2d import generate_profile
from .models import file_sha256
from .models_v2 import DatasetManifestV2, GeometrySpecV2
from .storage_v2 import DatasetStoreV2


def audit_dataset_v2(
    dataset: str | Path,
    *,
    rebuild_sample: int = 32,
    require_anchors: bool = True,
) -> dict[str, Any]:
    root = Path(dataset).resolve()
    store = DatasetStoreV2(root)
    manifest_path = root / "dataset_manifest.json"
    manifest = (
        DatasetManifestV2.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else None
    )
    if store.count() == 0:
        return {
            "valid": False,
            "checks": {"non_empty": False},
            "counts": {"generated": 0},
            "metrics": {},
            "hash_failures": [],
        }

    with store._lock, duckdb.connect(str(store.catalog_path)) as connection:
        generated, unique_generated = connection.execute(
            """
            SELECT count(*), count(DISTINCT geometry_id)
            FROM geometries_v2 WHERE origin = 'generated'
            """
        ).fetchone()
        rejected_records = connection.execute(
            """
            SELECT count(*) FROM geometries_v2
            WHERE origin = 'generated' AND validation_status = 'rejected'
            """
        ).fetchone()[0]
        wrong_lengths = connection.execute(
            """
            SELECT count(*) FROM geometries_v2
            WHERE origin = 'generated' AND (
              len(suction_x) != 201 OR len(suction_y) != 201 OR
              len(pressure_x) != 201 OR len(pressure_y) != 201 OR
              len(camber_x) != 201 OR len(camber_y) != 201 OR
              len(thickness) != 201 OR len(normal_x) != 201 OR len(normal_y) != 201
            )
            """
        ).fetchone()[0]
        group_rows = connection.execute(
            """
            SELECT intent_group_index, count(*),
                   count(DISTINCT camber_family || 'x' || thickness_family)
            FROM geometries_v2
            WHERE origin = 'generated' AND NOT is_extra
            GROUP BY intent_group_index
            """
        ).fetchall()
        extra_rows = connection.execute(
            """
            SELECT camber_family, thickness_family, count(*)
            FROM geometries_v2
            WHERE origin = 'generated' AND is_extra
            GROUP BY camber_family, thickness_family
            ORDER BY camber_family, thickness_family
            """
        ).fetchall()
        max_errors = connection.execute(
            """
            SELECT max(CAST(json_extract(validation_json,
                        '$.metrics.closure_error') AS DOUBLE)),
                   max(CAST(json_extract(validation_json,
                        '$.metrics.normal_offset_error') AS DOUBLE)),
                   max(CAST(json_extract(validation_json,
                        '$.metrics.leading_edge_radius_error') AS DOUBLE)),
                   max(CAST(json_extract(validation_json,
                        '$.metrics.trailing_edge_radius_error') AS DOUBLE)),
                   max(CAST(json_extract(validation_json,
                        '$.metrics.max_thickness_error') AS DOUBLE)),
                   max(CAST(json_extract(validation_json,
                        '$.metrics.max_thickness_x_error') AS DOUBLE)),
                   max(greatest(
                       CAST(json_extract(validation_json,
                            '$.metrics.inlet_metal_angle_error_deg') AS DOUBLE),
                       CAST(json_extract(validation_json,
                            '$.metrics.outlet_metal_angle_error_deg') AS DOUBLE)
                   ))
            FROM geometries_v2 WHERE origin = 'generated'
            """
        ).fetchone()
        sample_rows = connection.execute(
            """
            SELECT geometry_id, spec_json, suction_x, suction_y,
                   pressure_x, pressure_y
            FROM geometries_v2 WHERE origin = 'generated'
            ORDER BY geometry_id LIMIT ?
            """,
            [max(0, rebuild_sample)],
        ).fetchall()

    rebuild_error = 0.0
    rebuild_ids = True
    for geometry_id, spec_json, sx, sy, px, py in sample_rows:
        rebuilt = generate_profile(GeometrySpecV2.model_validate_json(spec_json))
        rebuild_ids = rebuild_ids and rebuilt.geometry_id == geometry_id
        suction, pressure, _, _ = rebuilt.arrays()
        rebuild_error = max(
            rebuild_error,
            float(np.max(np.abs(suction - np.column_stack([sx, sy])))),
            float(np.max(np.abs(pressure - np.column_stack([px, py])))),
        )

    hash_failures: list[str] = []
    if manifest is not None:
        for relative, expected in manifest.files.items():
            path = root / relative
            if not path.exists() or file_sha256(path) != expected:
                hash_failures.append(relative)

    expected = manifest.generated_valid if manifest else int(generated)
    group_contract = all(count == 9 and combinations == 9 for _, count, combinations in group_rows)
    expected_groups = manifest.complete_intent_groups if manifest else len(group_rows)
    expected_extras = manifest.extra_records if manifest else sum(row[2] for row in extra_rows)
    anchors = store.anchor_count()
    fits = store.fit_count()
    metrics = {
        "max_closure_error_c": float(max_errors[0] or 0.0),
        "max_normal_offset_error_c": float(max_errors[1] or 0.0),
        "max_le_radius_error_c": float(max_errors[2] or 0.0),
        "max_te_radius_error_c": float(max_errors[3] or 0.0),
        "max_thickness_error_c": float(max_errors[4] or 0.0),
        "max_thickness_x_error_c": float(max_errors[5] or 0.0),
        "max_metal_angle_error_deg": float(max_errors[6] or 0.0),
        "rebuild_sample": len(sample_rows),
        "max_rebuild_coordinate_error_c": rebuild_error,
    }
    checks = {
        "manifest_present": manifest is not None,
        "generated_count": int(generated) == int(expected),
        "unique_geometry_ids": generated == unique_generated,
        "generated_status": rejected_records == 0,
        "fixed_coordinate_length": wrong_lengths == 0,
        "paired_group_count": len(group_rows) == expected_groups,
        "paired_group_completeness": group_contract,
        "extra_count": sum(row[2] for row in extra_rows) == expected_extras,
        "closure": metrics["max_closure_error_c"] <= 1e-10,
        "normal_offset": metrics["max_normal_offset_error_c"] <= 1e-10,
        "edge_radius": max(metrics["max_le_radius_error_c"], metrics["max_te_radius_error_c"])
        <= 1e-8,
        "maximum_thickness": metrics["max_thickness_error_c"] <= 5e-4,
        "maximum_thickness_position": metrics["max_thickness_x_error_c"] <= 0.005,
        "metal_angles": metrics["max_metal_angle_error_deg"] <= 0.05,
        "artifact_hashes": not hash_failures,
        "deterministic_rebuild": rebuild_ids and rebuild_error <= 1e-12,
        "anchors": (anchors == 48 and fits == 432) if require_anchors else True,
    }
    return {
        "valid": all(checks.values()),
        "counts": {
            "generated": int(generated),
            "unique_generated": int(unique_generated),
            "complete_intent_groups": len(group_rows),
            "extras": int(sum(row[2] for row in extra_rows)),
            "anchors": anchors,
            "anchor_fits": fits,
            "rejected_proposals": store.rejected_count(),
        },
        "extra_distribution": {
            f"{camber}x{thickness}": int(count) for camber, thickness, count in extra_rows
        },
        "metrics": metrics,
        "checks": checks,
        "hash_failures": hash_failures,
    }
