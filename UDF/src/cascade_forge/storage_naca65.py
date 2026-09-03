from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .models import file_sha256
from .models_naca65 import (
    NACA65_BENCHMARK_VERSION,
    NACA65BenchmarkManifest,
    NACA65FitGeometry,
    NACA65FitReport,
    NACA65Geometry,
)


class NACA65BenchmarkStore:
    """Independent immutable Parquet/DuckDB store for NACA 65 fit studies."""

    _guard = threading.Lock()
    _locks: dict[Path, threading.RLock] = {}

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        manifest_path = self.root / "benchmark_manifest.json"
        if manifest_path.exists():
            version = json.loads(manifest_path.read_text(encoding="utf-8"))["benchmark_version"]
            if version != NACA65_BENCHMARK_VERSION:
                raise ValueError(
                    f"旧基准 {version} 保留只读；请使用 naca65-benchmark-v3.0.0 新目录"
                )
        self.case_dir = self.root / "tables" / "cases"
        self.run_dir = self.root / "runs"
        self.case_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)
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
                INSERT OR IGNORE INTO schema_migrations VALUES
                ('0001-naca65-benchmark', '1.0.0', current_timestamp,
                 'Independent NACA 65/65A native-to-B-spline benchmark')
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs_naca65 (
                    job_id VARCHAR PRIMARY KEY, job_type VARCHAR, status VARCHAR,
                    payload_json VARCHAR, result_json VARCHAR, error VARCHAR,
                    created_at TIMESTAMP, updated_at TIMESTAMP
                )
                """
            )
        self.refresh_view()

    def refresh_view(self) -> None:
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            if any(self.case_dir.glob("*.parquet")):
                pattern = self.case_dir.resolve().as_posix().replace("'", "''") + "/*.parquet"
                connection.execute(
                    f"CREATE OR REPLACE VIEW naca65_cases AS "
                    f"SELECT * FROM read_parquet('{pattern}', union_by_name=true)"
                )
            else:
                connection.execute(
                    """
                    CREATE OR REPLACE VIEW naca65_cases AS
                    SELECT CAST(NULL AS VARCHAR) fit_id,
                           CAST(NULL AS VARCHAR) native_id,
                           CAST(NULL AS VARCHAR) series_family,
                           CAST(NULL AS DOUBLE) thickness_ratio,
                           CAST(NULL AS DOUBLE) design_lift_coefficient,
                           CAST(NULL AS DOUBLE) loading_parameter_a,
                           CAST(NULL AS VARCHAR) fit_status
                    WHERE false
                    """
                )

    def append_case(
        self,
        reference: NACA65Geometry,
        report: NACA65FitReport,
        fitted: NACA65FitGeometry | None,
    ) -> Path | None:
        if report.fit_id in self.fit_ids():
            return None
        record = {
            "fit_id": report.fit_id,
            "native_id": reference.native_id,
            "designation": reference.designation,
            "series_family": reference.spec.family.value,
            "thickness_ratio": reference.spec.thickness_ratio,
            "design_lift_coefficient": reference.spec.design_lift_coefficient,
            "loading_parameter_a": reference.spec.loading_parameter_a,
            "fit_status": report.fit_status,
            "rms_distance_ratio": report.rms_distance_ratio,
            "max_distance_ratio": report.max_distance_ratio,
            "hausdorff_approx_ratio": report.hausdorff_approx_ratio,
            "core_rms_distance_ratio": report.core_rms_distance_ratio,
            "core_max_distance_ratio": report.core_max_distance_ratio,
            "elapsed_seconds": report.elapsed_seconds,
            "reference_json": reference.model_dump_json(),
            "fit_report_json": report.model_dump_json(),
            "fitted_geometry_json": fitted.model_dump_json() if fitted else None,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            index = max(
                [int(path.stem.split("-")[-1]) for path in self.case_dir.glob("part-*.parquet")],
                default=-1,
            ) + 1
            path = self.case_dir / f"part-{index:06d}.parquet"
            pq.write_table(pa.Table.from_pylist([record]), path, compression="zstd")
            self.refresh_view()
        return path

    def fit_ids(self) -> set[str]:
        if not any(self.case_dir.glob("*.parquet")):
            return set()
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            rows = connection.execute("SELECT fit_id FROM naca65_cases").fetchall()
            return {row[0] for row in rows}

    def native_ids(self) -> set[str]:
        if not any(self.case_dir.glob("*.parquet")):
            return set()
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            rows = connection.execute("SELECT native_id FROM naca65_cases").fetchall()
            return {row[0] for row in rows}

    def count(self) -> int:
        if not any(self.case_dir.glob("*.parquet")):
            return 0
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            return int(connection.execute("SELECT count(*) FROM naca65_cases").fetchone()[0])

    def status_distribution(self) -> dict[str, int]:
        if not any(self.case_dir.glob("*.parquet")):
            return {}
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            rows = connection.execute(
                "SELECT fit_status, count(*) FROM naca65_cases GROUP BY fit_status"
            ).fetchall()
        return {str(status): int(count) for status, count in rows}

    def list_cases(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str | None = None,
        family: str | None = None,
        sort: str = "designation",
        descending: bool = False,
    ) -> list[dict[str, Any]]:
        if not any(self.case_dir.glob("*.parquet")):
            return []
        allowed_sort = {
            "designation",
            "family",
            "thickness_ratio",
            "design_lift_coefficient",
            "loading_parameter_a",
            "fit_status",
            "rms_distance_ratio",
            "max_distance_ratio",
        }
        if sort not in allowed_sort:
            raise ValueError(f"不支持的排序字段: {sort}")
        where: list[str] = []
        parameters: list[Any] = []
        if status:
            where.append("fit_status = ?")
            parameters.append(status)
        if family:
            where.append("series_family = ?")
            parameters.append(family)
        clause = " WHERE " + " AND ".join(where) if where else ""
        direction = "DESC" if descending else "ASC"
        parameters.extend([max(1, min(limit, 1000)), max(0, offset)])
        sort_column = "series_family" if sort == "family" else sort
        query = (
            "SELECT fit_id, native_id, designation, series_family AS \"family\", "
            "thickness_ratio, "
            "design_lift_coefficient, loading_parameter_a, fit_status, "
            "rms_distance_ratio, max_distance_ratio, hausdorff_approx_ratio, "
            "core_rms_distance_ratio, core_max_distance_ratio, elapsed_seconds, created_at "
            f"FROM naca65_cases{clause} ORDER BY {sort_column} {direction} LIMIT ? OFFSET ?"
        )
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute(query, parameters)
            columns = [column[0] for column in result.description]
            return [dict(zip(columns, row, strict=True)) for row in result.fetchall()]

    def get_case(self, fit_id: str) -> dict[str, Any] | None:
        if not any(self.case_dir.glob("*.parquet")):
            return None
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute(
                "SELECT * FROM naca65_cases WHERE fit_id = ? LIMIT 1", [fit_id]
            )
            row = result.fetchone()
            if row is None:
                return None
            record = dict(zip([column[0] for column in result.description], row, strict=True))
        record["reference"] = json.loads(record.pop("reference_json"))
        record["fit_report"] = json.loads(record.pop("fit_report_json"))
        geometry_json = record.pop("fitted_geometry_json")
        record["fitted_geometry"] = json.loads(geometry_json) if geometry_json else None
        record["created_at"] = str(record["created_at"])
        return record

    def write_manifest(self, manifest: NACA65BenchmarkManifest) -> Path:
        path = self.run_dir / f"{manifest.run_id}.json"
        path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        (self.root / "benchmark_manifest.json").write_text(
            manifest.model_dump_json(indent=2), encoding="utf-8"
        )
        return path

    def latest_manifest(self) -> NACA65BenchmarkManifest | None:
        path = self.root / "benchmark_manifest.json"
        if not path.exists():
            return None
        return NACA65BenchmarkManifest.model_validate_json(path.read_text(encoding="utf-8"))

    def artifact_hashes(self) -> dict[str, str]:
        return {
            path.relative_to(self.root).as_posix(): file_sha256(path)
            for path in sorted(self.case_dir.glob("*.parquet"))
        }

    def create_job(self, job_type: str, payload: dict[str, Any]) -> str:
        job_id = "job_naca65_" + uuid.uuid4().hex
        with self._lock, duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                INSERT INTO jobs_naca65 VALUES
                (?, ?, 'queued', ?, NULL, NULL, current_timestamp, current_timestamp)
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
                UPDATE jobs_naca65 SET status=?, result_json=?, error=?,
                       updated_at=current_timestamp WHERE job_id=?
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
            result = connection.execute(
                "SELECT * FROM jobs_naca65 WHERE job_id = ?", [job_id]
            )
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
            rows = connection.execute(
                "SELECT job_id FROM jobs_naca65 ORDER BY created_at DESC LIMIT ?",
                [max(1, min(limit, 1000))],
            ).fetchall()
        return [job for row in rows if (job := self.get_job(row[0])) is not None]
