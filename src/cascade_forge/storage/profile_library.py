"""Additive native profile dataset; existing spline datasets remain untouched."""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import pyarrow as pa
import pyarrow.parquet as pq

from cascade_forge.domain.coordinates import coordinate_text
from cascade_forge.domain.labels import record_label


class ProfileLibrary:
    """SQLite index with complete native contracts and coordinates, no lossy fitting."""

    _guard = threading.Lock()
    _locks: dict[Path, threading.RLock] = {}

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "profiles.sqlite3"
        with self._guard:
            self._lock = self._locks.setdefault(self.path.resolve(), threading.RLock())
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS profiles "
                "(geometry_id TEXT PRIMARY KEY, family TEXT, name TEXT, payload TEXT)"
            )

            connection.execute(
                "CREATE TABLE IF NOT EXISTS deleted_profiles (geometry_id TEXT PRIMARY KEY)"
            )

    def deleted_ids(self) -> set[str]:
        with self._lock, sqlite3.connect(self.path) as connection:
            return {
                row[0] for row in connection.execute("SELECT geometry_id FROM deleted_profiles")
            }

    def delete(self, geometry_id: str) -> None:
        """Remove the database sample, preserving source files and immutable datasets."""
        with self._lock, sqlite3.connect(self.path) as connection:
            connection.execute("INSERT OR IGNORE INTO deleted_profiles VALUES (?)", (geometry_id,))

    def save(self, record: dict[str, Any], *, write_files: bool = True) -> None:
        with self._lock:
            self._save(record, write_files=write_files)

    def _save(self, record: dict[str, Any], *, write_files: bool) -> None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload FROM profiles WHERE geometry_id = ?", (record["geometry_id"],)
            ).fetchone()
        if row is not None:
            record = json.loads(row[0])
        files = {}
        if write_files:
            for format in ("csv", "dat"):
                files[self.root / f"{record['geometry_id']}.{format}"] = coordinate_text(
                    record, format
                )
            files[self.root / f"{record['geometry_id']}.json"] = json.dumps(
                record, ensure_ascii=False, indent=2, allow_nan=False
            )
            for path, content in files.items():
                if path.is_symlink() or (
                    path.exists()
                    and path.read_text(encoding="utf-8") != content.replace("\r\n", "\n")
                ):
                    raise ValueError(f"已有文件 {path.name} 内容不同，不覆盖用户文件")
        with sqlite3.connect(self.path, timeout=30) as connection:
            if record.get("source_file"):
                connection.execute(
                    "DELETE FROM profiles WHERE family = 'imported' AND "
                    "json_extract(payload, '$.source_file') = ? AND geometry_id != ?",
                    (record["source_file"], record["geometry_id"]),
                )
            connection.execute(
                "INSERT OR IGNORE INTO profiles VALUES (?, ?, ?, ?)",
                (
                    record["geometry_id"],
                    record["family"],
                    record["name"],
                    json.dumps(record, ensure_ascii=False, allow_nan=False),
                ),
            )
            connection.execute(
                "DELETE FROM deleted_profiles WHERE geometry_id = ?", (record["geometry_id"],)
            )
        for path, content in files.items():
            if not path.exists():
                with path.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(content)

    def get(self, geometry_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload FROM profiles WHERE geometry_id = ? AND geometry_id NOT IN "
                "(SELECT geometry_id FROM deleted_profiles)",
                (geometry_id,),
            ).fetchone()
        if row is None:
            return None
        record = json.loads(row[0])
        record["label"] = record_label(record)
        return record

    def list(self, search: str = "", family: str = "") -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT payload FROM profiles WHERE geometry_id NOT IN "
                "(SELECT geometry_id FROM deleted_profiles) AND "
                "(? = '' OR family = ?) AND "
                "(? = '' OR instr(geometry_id, ?) > 0 OR instr(name, ?) > 0) "
                "ORDER BY rowid DESC",
                (family, family, search, search, search),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def summaries(self, search: str = "", family: str = "") -> list[dict[str, Any]]:
        result = []
        for record in self.list(family=family):
            label = record_label(record)
            if (
                search
                and search.casefold()
                not in (record["geometry_id"] + " " + record["name"] + " " + label).casefold()
            ):
                continue
            result.append(
                {
                    "geometry_id": record["geometry_id"],
                    "family": record["family"],
                    "name": record["name"],
                    "label": label,
                    "source": "profile-library",
                }
            )
        return result

    def export_dataset(self) -> bytes:
        records = self.list()
        sink = io.BytesIO()
        with ZipFile(sink, "w", ZIP_DEFLATED) as archive:
            archive.writestr("profiles.json", json.dumps(records, ensure_ascii=False))
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": "native-profile-1.0",
                        "count": len(records),
                        "coordinate_units": "per-profile"
                        if any(record["family"] == "imported" for record in records)
                        else "chord-normalized",
                        "contains_native_contracts": all(
                            record["family"] != "imported" for record in records
                        ),
                        "profiles": [
                            {
                                "geometry_id": record["geometry_id"],
                                "family": record["family"],
                                "coordinate_convention": record.get("coordinate_convention", ""),
                                "native_sha256": hashlib.sha256(
                                    json.dumps(
                                        record["native_geometry"],
                                        sort_keys=True,
                                        ensure_ascii=False,
                                    ).encode("utf-8")
                                ).hexdigest(),
                            }
                            for record in records
                        ],
                    },
                    indent=2,
                ),
            )
            if records:
                table = pa.Table.from_pylist(
                    [
                        {
                            "geometry_id": r["geometry_id"],
                            "family": r["family"],
                            "spec_json": json.dumps(r["spec"]),
                            "suction": r["suction"],
                            "pressure": r["pressure"],
                        }
                        for r in records
                    ]
                )
                parquet = io.BytesIO()
                pq.write_table(table, parquet)
                archive.writestr("profiles.parquet", parquet.getvalue())
            for record in records:
                for format in ("csv", "dat"):
                    archive.writestr(
                        f"coordinates/{record['geometry_id']}.{format}",
                        coordinate_text(record, format),
                    )
        return sink.getvalue()
