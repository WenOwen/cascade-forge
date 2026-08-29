from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import numpy as np

from .geometry2d import generate_profile
from .models import DatasetManifest, GeometrySpec, file_sha256
from .storage import DatasetStore


def audit_dataset(dataset: str | Path, *, rebuild_sample: int = 128) -> dict[str, Any]:
    """对数据规模、不可变制品和生成可复现性执行交付级审计。"""

    root = Path(dataset).resolve()
    store = DatasetStore(root)
    manifest_path = root / "dataset_manifest.json"
    manifest = (
        DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else None
    )
    with store._lock, duckdb.connect(str(store.catalog_path)) as connection:
        total, unique = connection.execute(
            "SELECT count(*), count(DISTINCT geometry_id) FROM geometries"
        ).fetchone()
        status_rows = connection.execute(
            """
            SELECT origin, validation_status, count(*)
            FROM geometries GROUP BY origin, validation_status
            ORDER BY origin, validation_status
            """
        ).fetchall()
        generated_invalid = connection.execute(
            """
            SELECT count(*) FROM geometries
            WHERE origin = 'generated' AND validation_status = 'rejected'
            """
        ).fetchone()[0]
        wrong_point_count = connection.execute(
            """
            SELECT count(*) FROM geometries
            WHERE origin = 'generated' AND (
                len(suction_x) != 201 OR len(suction_y) != 201 OR
                len(pressure_x) != 201 OR len(pressure_y) != 201 OR
                len(camber_x) != 201 OR len(camber_y) != 201 OR len(thickness) != 201
            )
            """
        ).fetchone()[0]
        max_closure_error = connection.execute(
            """
            SELECT max(greatest(abs(camber_y[1]), abs(camber_y[len(camber_y)])))
            FROM geometries WHERE origin = 'generated'
            """
        ).fetchone()[0]
        max_thickness_error = connection.execute(
            """
            SELECT max(abs(
                max_thickness_ratio -
                CAST(json_extract(spec_json, '$.max_thickness_ratio') AS DOUBLE)
            ))
            FROM geometries WHERE origin = 'generated'
            """
        ).fetchone()[0]
        max_metal_angle_error = connection.execute(
            """
            SELECT max(greatest(
                abs(
                    degrees(atan2(camber_y[2] - camber_y[1], camber_x[2] - camber_x[1]))
                    + realized_stagger_deg
                    - CASE WHEN json_extract_string(spec_json, '$.handedness') = 'right'
                        THEN CAST(json_extract(spec_json, '$.inlet_metal_angle_deg') AS DOUBLE)
                        ELSE -CAST(json_extract(spec_json, '$.inlet_metal_angle_deg') AS DOUBLE)
                      END
                ),
                abs(
                    degrees(atan2(
                        camber_y[len(camber_y)] - camber_y[len(camber_y) - 1],
                        camber_x[len(camber_x)] - camber_x[len(camber_x) - 1]
                    ))
                    + realized_stagger_deg
                    - CASE WHEN json_extract_string(spec_json, '$.handedness') = 'right'
                        THEN CAST(json_extract(spec_json, '$.inlet_metal_angle_deg') AS DOUBLE)
                             - CAST(json_extract(spec_json, '$.turning_angle_deg') AS DOUBLE)
                        ELSE -(
                            CAST(json_extract(spec_json, '$.inlet_metal_angle_deg') AS DOUBLE)
                            - CAST(json_extract(spec_json, '$.turning_angle_deg') AS DOUBLE)
                        )
                      END
                )
            ))
            FROM geometries WHERE origin = 'generated'
            """
        ).fetchone()[0]
        sample_rows = connection.execute(
            """
            SELECT geometry_id, spec_json, suction_x, suction_y, pressure_x, pressure_y
            FROM geometries WHERE origin = 'generated'
            ORDER BY geometry_id LIMIT ?
            """,
            [max(0, rebuild_sample)],
        ).fetchall()

    maximum_rebuild_error = 0.0
    rebuilt_ids_match = True
    for geometry_id, spec_json, sx, sy, px, py in sample_rows:
        rebuilt = generate_profile(GeometrySpec.model_validate_json(spec_json))
        rebuilt_ids_match = rebuilt_ids_match and rebuilt.geometry_id == geometry_id
        suction, pressure, _, _ = rebuilt.arrays()
        stored_suction = np.column_stack([sx, sy])
        stored_pressure = np.column_stack([px, py])
        maximum_rebuild_error = max(
            maximum_rebuild_error,
            float(np.max(np.abs(suction - stored_suction))),
            float(np.max(np.abs(pressure - stored_pressure))),
        )

    hash_failures: list[str] = []
    if manifest is not None:
        for relative, expected in manifest.files.items():
            path = root / relative
            if not path.exists() or file_sha256(path) != expected:
                hash_failures.append(relative)

    generated = store.count("generated")
    anchors = store.count("anchor")
    rejected = store.rejected_count()
    expected_generated = manifest.generated_valid if manifest else generated
    expected_anchors = manifest.anchors if manifest else anchors
    checks = {
        "manifest_present": manifest is not None,
        "generated_count": generated == expected_generated,
        "anchor_count": anchors == expected_anchors,
        "unique_geometry_ids": total == unique,
        "generated_status": generated_invalid == 0,
        "fixed_coordinate_length": wrong_point_count == 0,
        "camber_closure": float(max_closure_error or 0.0) <= 1e-10,
        "metal_angles": float(max_metal_angle_error or 0.0) <= 0.05,
        "maximum_thickness": float(max_thickness_error or 0.0) <= 5e-4,
        "artifact_hashes": not hash_failures,
        "deterministic_rebuild": rebuilt_ids_match and maximum_rebuild_error <= 1e-12,
    }
    return {
        "valid": all(checks.values()),
        "counts": {
            "total": int(total),
            "generated": generated,
            "anchors": anchors,
            "rejected_proposals": rejected,
            "unique_geometry_ids": int(unique),
        },
        "status_distribution": {
            f"{origin}:{status}": int(count) for origin, status, count in status_rows
        },
        "metrics": {
            "max_camber_closure_error_c": float(max_closure_error or 0.0),
            "max_metal_angle_error_deg": float(max_metal_angle_error or 0.0),
            "max_thickness_error_c": float(max_thickness_error or 0.0),
            "rebuild_sample": len(sample_rows),
            "max_rebuild_coordinate_error_c": maximum_rebuild_error,
        },
        "checks": checks,
        "hash_failures": hash_failures,
    }
