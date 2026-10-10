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

from cascade_forge.domain.models import (
    AirfoilGeometryV2,
    BSplineCurveSpec,
    DatasetManifestV2,
    GeometryFeaturesV2,
    GeometrySpecV2,
    ValidationReport,
    file_sha256,
)


class DatasetStoreV2:
    """schema 2.1 的单写者 DuckDB 目录与不可变 Parquet 分区。"""

    _guard = threading.Lock()
    _locks: dict[Path, threading.RLock] = {}

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.geometry_dir = self.root / "tables" / "geometries"
        self.rejected_dir = self.root / "tables" / "rejected"
        self.batch_dir = self.root / "batches"
        for directory in (
            self.geometry_dir,
            self.rejected_dir,
            self.batch_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.root / "catalog.duckdb"
        with self._guard:
            self._lock = self._locks.setdefault(self.catalog_path, threading.RLock())
        self._initialize_catalog()

    def _initialize_catalog(self) -> None:
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    migration_id VARCHAR PRIMARY KEY,
                    schema_version VARCHAR NOT NULL,
                    applied_at TIMESTAMP NOT NULL,
                    note VARCHAR
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs_v2 (
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
                INSERT OR IGNORE INTO schema_migrations VALUES
                ('0002-schema-v2.1', '2.1.0', current_timestamp,
                 'Unified B-spline G2 geometry and independent stagger placement')
                """
            )
        self.refresh_views()

    def refresh_views(self) -> None:
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            self._replace_parquet_view(
                connection,
                "geometries_v2",
                self.geometry_dir,
                "SELECT CAST(NULL AS VARCHAR) geometry_id, "
                "CAST(NULL AS VARCHAR) intent_id WHERE false",
            )
            self._replace_parquet_view(
                connection,
                "rejected_v2",
                self.rejected_dir,
                "SELECT CAST(NULL AS BIGINT) proposal_index WHERE false",
            )
            if any(self.geometry_dir.glob("*.parquet")):
                connection.execute(
                    """
                    CREATE OR REPLACE VIEW geometry_search_v2 AS
                    SELECT geometry_id, intent_id, origin, source_id,
                           kernel, validation_status,
                           inlet_metal_angle_deg, outlet_metal_angle_deg,
                           max_thickness_ratio, max_thickness_x_ratio,
                           proposal_index, created_at
                    FROM geometries_v2
                    """
                )
            else:
                connection.execute(
                    """
                    CREATE OR REPLACE VIEW geometry_search_v2 AS
                    SELECT CAST(NULL AS VARCHAR) geometry_id,
                           CAST(NULL AS VARCHAR) intent_id,
                           CAST(NULL AS VARCHAR) origin,
                           CAST(NULL AS VARCHAR) source_id,
                           CAST(NULL AS VARCHAR) kernel,
                           CAST(NULL AS VARCHAR) validation_status,
                           CAST(NULL AS DOUBLE) inlet_metal_angle_deg,
                           CAST(NULL AS DOUBLE) outlet_metal_angle_deg,
                           CAST(NULL AS DOUBLE) max_thickness_ratio,
                           CAST(NULL AS DOUBLE) max_thickness_x_ratio,
                           CAST(NULL AS BIGINT) proposal_index,
                           CAST(NULL AS TIMESTAMP) created_at
                    WHERE false
                    """
                )

    def _replace_parquet_view(
        self,
        connection: duckdb.DuckDBPyConnection,
        name: str,
        directory: Path,
        empty_query: str,
    ) -> None:
        if any(directory.glob("*.parquet")):
            pattern = _sql_path(directory / "*.parquet")
            connection.execute(
                f"CREATE OR REPLACE VIEW {name} AS "
                f"SELECT * FROM read_parquet('{pattern}', union_by_name=true)"
            )
        else:
            connection.execute(f"CREATE OR REPLACE VIEW {name} AS {empty_query}")

    def append_geometries(
        self,
        rows: Iterable[
            tuple[
                AirfoilGeometryV2,
                GeometryFeaturesV2,
                ValidationReport,
                dict[str, Any],
            ]
        ],
        *,
        batch_metadata: dict[str, Any] | None = None,
    ) -> Path | None:
        records = [_geometry_record_v2(*row) for row in rows]
        if not records:
            return None
        with self._lock:
            index = self._next_index(self.geometry_dir)
            path = self.geometry_dir / f"part-{index:06d}.parquet"
            pq.write_table(pa.Table.from_pylist(records), path, compression="zstd")
            batch = {
                "batch_index": index,
                "row_count": len(records),
                "first_geometry_id": records[0]["geometry_id"],
                "last_geometry_id": records[-1]["geometry_id"],
                "created_at": datetime.now(UTC).isoformat(),
                "parquet": path.relative_to(self.root).as_posix(),
                "parquet_sha256": file_sha256(path),
                **(batch_metadata or {}),
            }
            (self.batch_dir / f"batch-{index:06d}.json").write_text(
                json.dumps(batch, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self.refresh_views()
        return path

    def append_rejected(self, records: list[dict[str, Any]]) -> Path | None:
        if not records:
            return None
        now = datetime.now(UTC).isoformat()
        normalized = [
            {
                "proposal_index": int(record["proposal_index"]),
                "intent_id": record.get("intent_id"),
                "parameters_json": json.dumps(
                    record.get("parameters", {}),
                    sort_keys=True,
                    ensure_ascii=False,
                ),
                "reasons_json": json.dumps(
                    record.get("reasons", []),
                    sort_keys=True,
                    ensure_ascii=False,
                ),
                "created_at": now,
            }
            for record in records
        ]
        with self._lock:
            path = self.rejected_dir / (f"part-{self._next_index(self.rejected_dir):06d}.parquet")
            pq.write_table(pa.Table.from_pylist(normalized), path, compression="zstd")
            self.refresh_views()
        return path

    @staticmethod
    def _search_filters(origin=None, status=None, search=None):
        where, parameters = [], []
        for column, value in (("origin", origin), ("validation_status", status)):
            if value:
                where.append(f"{column} = ?")
                parameters.append(value)
        if search and search.strip():
            where.append("contains(lower(geometry_id), lower(?))")
            parameters.append(search.strip())
        return (" WHERE " + " AND ".join(where) if where else ""), parameters

    def count(
        self,
        origin: str | None = None,
        *,
        status: str | None = None,
        search: str | None = None,
    ) -> int:
        if not any(self.geometry_dir.glob("*.parquet")):
            return 0
        clause, parameters = self._search_filters(origin, status, search)
        query = "SELECT count(*) FROM geometries_v2" + clause
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            return int(connection.execute(query, parameters).fetchone()[0])

    def rejected_count(self) -> int:
        if not any(self.rejected_dir.glob("*.parquet")):
            return 0
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            return int(connection.execute("SELECT count(*) FROM rejected_v2").fetchone()[0])

    def geometry_ids(self) -> set[str]:
        if not any(self.geometry_dir.glob("*.parquet")):
            return set()
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            return {
                row[0]
                for row in connection.execute("SELECT geometry_id FROM geometries_v2").fetchall()
            }

    def get_geometry(self, geometry_id: str) -> AirfoilGeometryV2 | None:
        if not any(self.geometry_dir.glob("*.parquet")):
            return None
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute(
                "SELECT * FROM geometries_v2 WHERE geometry_id = ? LIMIT 1",
                [geometry_id],
            )
            row = result.fetchone()
            if row is None:
                return None
            record = dict(zip([column[0] for column in result.description], row, strict=True))
        return _record_geometry_v2(record)

    def list_geometries(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        origin: str | None = None,
        sort: str = "geometry_id",
        descending: bool = False,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        allowed_sort = {
            "geometry_id",
            "max_thickness_ratio",
            "max_thickness_x_ratio",
            "inlet_metal_angle_deg",
            "created_at",
        }
        if sort not in allowed_sort:
            raise ValueError(f"不支持的排序字段: {sort}")
        if not any(self.geometry_dir.glob("*.parquet")):
            return []
        clause, parameters = self._search_filters(origin, status, search)
        direction = "DESC" if descending else "ASC"
        query = (
            f"SELECT * FROM geometry_search_v2{clause} ORDER BY {sort} {direction}, "
            "geometry_id ASC LIMIT ? OFFSET ?"
        )
        parameters.extend([max(1, min(limit, 1000)), max(0, offset)])
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute(query, parameters)
            columns = [column[0] for column in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def write_manifest(self, manifest: DatasetManifestV2) -> Path:
        path = self.root / "dataset_manifest.json"
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        return path

    def artifact_hashes(self) -> dict[str, str]:
        return {
            path.relative_to(self.root).as_posix(): file_sha256(path)
            for path in sorted((self.root / "tables").rglob("*.parquet"))
        }

    def batch_manifests(self) -> list[str]:
        return [
            path.relative_to(self.root).as_posix()
            for path in sorted(self.batch_dir.glob("batch-*.json"))
        ]

    def create_job(self, job_type: str, payload: dict[str, Any]) -> str:
        job_id = "job_v2_" + uuid.uuid4().hex
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                INSERT INTO jobs_v2 VALUES (?, ?, 'queued', ?, NULL, NULL,
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
                UPDATE jobs_v2 SET status = ?, result_json = ?, error = ?,
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
            result = connection.execute("SELECT * FROM jobs_v2 WHERE job_id = ?", [job_id])
            row = result.fetchone()
            if row is None:
                return None
            record = dict(zip([column[0] for column in result.description], row, strict=True))
        for key in ("payload_json", "result_json"):
            if record[key]:
                record[key.removesuffix("_json")] = json.loads(record[key])
            record.pop(key)
        record["created_at"] = str(record["created_at"])
        record["updated_at"] = str(record["updated_at"])
        return record

    def list_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            ids = connection.execute(
                "SELECT job_id FROM jobs_v2 ORDER BY created_at DESC LIMIT ?",
                [max(1, min(limit, 1000))],
            ).fetchall()
        return [self.get_job(row[0]) for row in ids]

    @staticmethod
    def _next_index(directory: Path) -> int:
        indices = [int(path.stem.split("-")[-1]) for path in directory.glob("part-*.parquet")]
        return max(indices, default=-1) + 1


def _geometry_record_v2(
    geometry: AirfoilGeometryV2,
    features: GeometryFeaturesV2,
    report: ValidationReport,
    context: dict[str, Any],
) -> dict[str, Any]:
    suction, pressure, camber, thickness = geometry.arrays()
    normals = geometry.normals
    return {
        "geometry_id": geometry.geometry_id,
        "intent_id": geometry.intent_id,
        "origin": geometry.origin,
        "source_id": geometry.source_id,
        "kernel": features.kernel,
        "validation_status": report.status.value,
        "proposal_index": context.get("proposal_index"),
        "spec_json": geometry.spec.model_dump_json(),
        "explicit_curves_json": json.dumps(
            {
                "camber": geometry.spec.camber_curve.model_dump(mode="json"),
                "suction_thickness": geometry.spec.suction_thickness_curve.model_dump(mode="json"),
                "pressure_thickness": geometry.spec.pressure_thickness_curve.model_dump(
                    mode="json"
                ),
            },
            sort_keys=True,
            ensure_ascii=False,
        ),
        "edge_json": geometry.spec.edge.model_dump_json(),
        "edge_metrics_json": json.dumps(geometry.edge_metrics, sort_keys=True, ensure_ascii=False),
        "resolution_residuals_json": json.dumps(
            geometry.spec.resolution_residuals,
            sort_keys=True,
            ensure_ascii=False,
        ),
        "metadata_json": json.dumps(geometry.metadata, sort_keys=True, ensure_ascii=False),
        "features_json": features.model_dump_json(),
        "validation_json": report.model_dump_json(),
        "inlet_metal_angle_deg": features.inlet_metal_angle_deg,
        "outlet_metal_angle_deg": features.outlet_metal_angle_deg,
        "max_thickness_ratio": features.max_thickness_ratio,
        "max_thickness_x_ratio": features.max_thickness_x_ratio,
        "leading_edge_radius_ratio": features.leading_edge_radius_ratio,
        "trailing_edge_radius_ratio": features.trailing_edge_radius_ratio,
        "suction_x": suction[:, 0].tolist(),
        "suction_y": suction[:, 1].tolist(),
        "pressure_x": pressure[:, 0].tolist(),
        "pressure_y": pressure[:, 1].tolist(),
        "camber_x": camber[:, 0].tolist(),
        "camber_y": camber[:, 1].tolist(),
        "thickness": thickness.tolist(),
        "normal_x": [item[0] for item in normals],
        "normal_y": [item[1] for item in normals],
        "suction_surface_curve_json": geometry.suction_surface_curve.model_dump_json(),
        "pressure_surface_curve_json": geometry.pressure_surface_curve.model_dump_json(),
        "leading_osculating_circle_json": json.dumps(geometry.leading_osculating_circle),
        "trailing_osculating_circle_json": json.dumps(geometry.trailing_osculating_circle),
        "realized_stagger_deg": geometry.realized_stagger_deg,
        "created_at": datetime.now(UTC).isoformat(),
    }


def _record_geometry_v2(record: dict[str, Any]) -> AirfoilGeometryV2:
    def points(x_name: str, y_name: str) -> list[list[float]]:
        return [list(item) for item in zip(record[x_name], record[y_name], strict=True)]

    return AirfoilGeometryV2(
        geometry_id=record["geometry_id"],
        intent_id=record["intent_id"],
        spec=GeometrySpecV2.model_validate_json(record["spec_json"]),
        origin=record["origin"],
        source_id=record["source_id"],
        suction=points("suction_x", "suction_y"),
        pressure=points("pressure_x", "pressure_y"),
        camber=points("camber_x", "camber_y"),
        thickness=record["thickness"],
        normals=points("normal_x", "normal_y"),
        suction_surface_curve=BSplineCurveSpec.model_validate_json(
            record["suction_surface_curve_json"]
        ),
        pressure_surface_curve=BSplineCurveSpec.model_validate_json(
            record["pressure_surface_curve_json"]
        ),
        leading_osculating_circle=json.loads(record["leading_osculating_circle_json"]),
        trailing_osculating_circle=json.loads(record["trailing_osculating_circle_json"]),
        realized_stagger_deg=record["realized_stagger_deg"],
        edge_metrics=json.loads(record["edge_metrics_json"]),
        metadata=json.loads(record["metadata_json"]),
    )


def _sql_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")
