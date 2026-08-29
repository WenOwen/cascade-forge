from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .models import (
    AirfoilGeometry,
    DatasetManifest,
    GeometryFeatures,
    ValidationReport,
    file_sha256,
)


class DatasetStore:
    """DuckDB 查询目录 + 不可变 Parquet 分区。"""

    _lock_guard = threading.Lock()
    _catalog_locks: dict[Path, threading.RLock] = {}

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.geometry_dir = self.root / "tables" / "geometries"
        self.rejected_dir = self.root / "tables" / "rejected"
        self.geometry_dir.mkdir(parents=True, exist_ok=True)
        self.rejected_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "catalog.duckdb"
        with self._lock_guard:
            self._lock = self._catalog_locks.setdefault(self.catalog_path, threading.RLock())
        self._initialize_catalog()

    def _initialize_catalog(self) -> None:
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    schema_version VARCHAR PRIMARY KEY,
                    created_at TIMESTAMP,
                    note VARCHAR
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id VARCHAR PRIMARY KEY,
                    job_type VARCHAR,
                    status VARCHAR,
                    payload_json VARCHAR,
                    result_json VARCHAR,
                    error VARCHAR,
                    created_at TIMESTAMP,
                    updated_at TIMESTAMP
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_info VALUES
                ('1.0.0', current_timestamp, 'Cascade Forge geometry schema v1')
                """
            )
        self.refresh_views()

    def refresh_views(self) -> None:
        geometry_files = list(self.geometry_dir.glob("*.parquet"))
        rejected_files = list(self.rejected_dir.glob("*.parquet"))
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            if geometry_files:
                pattern = _sql_path(self.geometry_dir / "*.parquet")
                connection.execute(
                    "CREATE OR REPLACE VIEW geometries AS "
                    f"SELECT * FROM read_parquet('{pattern}', union_by_name = true)"
                )
            else:
                connection.execute(
                    """
                    CREATE OR REPLACE VIEW geometries AS
                    SELECT CAST(NULL AS VARCHAR) geometry_id WHERE false
                    """
                )
            if rejected_files:
                pattern = _sql_path(self.rejected_dir / "*.parquet")
                connection.execute(
                    "CREATE OR REPLACE VIEW rejected AS "
                    f"SELECT * FROM read_parquet('{pattern}', union_by_name = true)"
                )
            else:
                connection.execute(
                    """
                    CREATE OR REPLACE VIEW rejected AS
                    SELECT CAST(NULL AS BIGINT) proposal_index WHERE false
                    """
                )

    def append_geometries(
        self,
        rows: Iterable[tuple[AirfoilGeometry, GeometryFeatures, ValidationReport]],
    ) -> Path | None:
        records = [_geometry_record(*row) for row in rows]
        if not records:
            return None
        with self._lock:
            path = self.geometry_dir / f"part-{self._next_index(self.geometry_dir):06d}.parquet"
            pq.write_table(pa.Table.from_pylist(records), path, compression="zstd")
            self.refresh_views()
        return path

    def append_rejected(self, records: list[dict[str, Any]]) -> Path | None:
        if not records:
            return None
        normalized = []
        now = datetime.now(UTC).isoformat()
        for record in records:
            normalized.append(
                {
                    "proposal_index": int(record["proposal_index"]),
                    "geometry_id": record.get("geometry_id"),
                    "parameters_json": json.dumps(
                        record.get("parameters", {}), sort_keys=True, ensure_ascii=False
                    ),
                    "reasons_json": json.dumps(
                        record.get("reasons", []), sort_keys=True, ensure_ascii=False
                    ),
                    "created_at": now,
                }
            )
        with self._lock:
            path = self.rejected_dir / f"part-{self._next_index(self.rejected_dir):06d}.parquet"
            pq.write_table(pa.Table.from_pylist(normalized), path, compression="zstd")
            self.refresh_views()
        return path

    def count(self, origin: str | None = None) -> int:
        if not any(self.geometry_dir.glob("*.parquet")):
            return 0
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            if origin is None:
                return int(connection.execute("SELECT count(*) FROM geometries").fetchone()[0])
            return int(
                connection.execute(
                    "SELECT count(*) FROM geometries WHERE origin = ?", [origin]
                ).fetchone()[0]
            )

    def rejected_count(self) -> int:
        if not any(self.rejected_dir.glob("*.parquet")):
            return 0
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            return int(connection.execute("SELECT count(*) FROM rejected").fetchone()[0])

    def geometry_ids(self) -> set[str]:
        if not any(self.geometry_dir.glob("*.parquet")):
            return set()
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            return {
                row[0]
                for row in connection.execute("SELECT geometry_id FROM geometries").fetchall()
            }

    def get_geometry(self, geometry_id: str) -> AirfoilGeometry | None:
        if not any(self.geometry_dir.glob("*.parquet")):
            return None
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute(
                "SELECT * FROM geometries WHERE geometry_id = ? LIMIT 1", [geometry_id]
            )
            row = result.fetchone()
            if row is None:
                return None
            record = dict(zip([item[0] for item in result.description], row, strict=True))
        return _record_geometry(record)

    def list_geometries(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        origin: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if not any(self.geometry_dir.glob("*.parquet")):
            return []
        where = []
        parameters: list[Any] = []
        if origin:
            where.append("origin = ?")
            parameters.append(origin)
        if status:
            where.append("validation_status = ?")
            parameters.append(status)
        clause = " WHERE " + " AND ".join(where) if where else ""
        query = f"""
            SELECT geometry_id, origin, source_id, validation_status,
                   inlet_metal_angle_deg, outlet_metal_angle_deg,
                   max_thickness_ratio, max_thickness_x_ratio,
                   max_camber_ratio, realized_stagger_deg
            FROM geometries{clause}
            ORDER BY geometry_id LIMIT ? OFFSET ?
        """
        parameters.extend([max(1, min(limit, 1000)), max(0, offset)])
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute(query, parameters)
            columns = [item[0] for item in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def write_manifest(self, manifest: DatasetManifest) -> Path:
        path = self.root / "dataset_manifest.json"
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return path

    def artifact_hashes(self) -> dict[str, str]:
        files: dict[str, str] = {}
        for path in sorted((self.root / "tables").rglob("*.parquet")):
            files[path.relative_to(self.root).as_posix()] = file_sha256(path)
        return files

    def create_job(self, job_type: str, payload: dict[str, Any]) -> str:
        job_id = "job_" + uuid.uuid4().hex
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                INSERT INTO jobs VALUES (?, ?, 'queued', ?, NULL, NULL,
                                         current_timestamp, current_timestamp)
                """,
                [job_id, job_type, json.dumps(payload, ensure_ascii=False)],
            )
        return job_id

    def update_job(
        self,
        job_id: str,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                UPDATE jobs SET status = ?, result_json = ?, error = ?,
                                updated_at = current_timestamp
                WHERE job_id = ?
                """,
                [
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    job_id,
                ],
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute("SELECT * FROM jobs WHERE job_id = ?", [job_id])
            row = result.fetchone()
            if row is None:
                return None
            record = dict(zip([item[0] for item in result.description], row, strict=True))
        for key in ("payload_json", "result_json"):
            if record[key]:
                record[key.removesuffix("_json")] = json.loads(record[key])
            record.pop(key)
        record["created_at"] = str(record["created_at"])
        record["updated_at"] = str(record["updated_at"])
        return record

    @staticmethod
    def _next_index(directory: Path) -> int:
        indices = [int(path.stem.split("-")[-1]) for path in directory.glob("part-*.parquet")]
        return max(indices, default=-1) + 1


def _geometry_record(
    geometry: AirfoilGeometry,
    features: GeometryFeatures,
    report: ValidationReport,
) -> dict[str, Any]:
    suction, pressure, camber, thickness = geometry.arrays()
    spec_json = geometry.spec.model_dump_json() if geometry.spec is not None else None
    return {
        "geometry_id": geometry.geometry_id,
        "origin": geometry.origin,
        "source_id": geometry.source_id,
        "validation_status": report.status.value,
        "spec_json": spec_json,
        "metadata_json": json.dumps(geometry.metadata, sort_keys=True, ensure_ascii=False),
        "validation_json": report.model_dump_json(),
        "features_json": features.model_dump_json(),
        "inlet_metal_angle_deg": features.inlet_metal_angle_deg,
        "outlet_metal_angle_deg": features.outlet_metal_angle_deg,
        "turning_angle_deg": features.turning_angle_deg,
        "realized_stagger_deg": geometry.realized_stagger_deg,
        "max_thickness_ratio": features.max_thickness_ratio,
        "max_thickness_x_ratio": features.max_thickness_x_ratio,
        "max_camber_ratio": features.max_camber_ratio,
        "leading_edge_radius_ratio": features.leading_edge_radius_ratio,
        "trailing_edge_thickness_ratio": features.trailing_edge_thickness_ratio,
        "suction_x": suction[:, 0].tolist(),
        "suction_y": suction[:, 1].tolist(),
        "pressure_x": pressure[:, 0].tolist(),
        "pressure_y": pressure[:, 1].tolist(),
        "camber_x": camber[:, 0].tolist(),
        "camber_y": camber[:, 1].tolist(),
        "thickness": thickness.tolist(),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _record_geometry(record: dict[str, Any]) -> AirfoilGeometry:
    from .models import GeometrySpec

    spec = GeometrySpec.model_validate_json(record["spec_json"]) if record["spec_json"] else None
    suction = list(map(list, zip(record["suction_x"], record["suction_y"], strict=True)))
    pressure = list(map(list, zip(record["pressure_x"], record["pressure_y"], strict=True)))
    camber = list(map(list, zip(record["camber_x"], record["camber_y"], strict=True)))
    return AirfoilGeometry(
        geometry_id=record["geometry_id"],
        spec=spec,
        origin=record["origin"],
        source_id=record["source_id"],
        suction=suction,
        pressure=pressure,
        camber=camber,
        thickness=record["thickness"],
        realized_stagger_deg=record["realized_stagger_deg"],
        metadata=json.loads(record["metadata_json"]),
    )


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")
