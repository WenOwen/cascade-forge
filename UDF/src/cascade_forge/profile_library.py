"""Additive native profile dataset; existing spline datasets remain untouched."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .circular_arc import generate_arc_profile
from .geometry2d import generate_profile
from .models_circular_arc import ArcProfileSpec
from .models_naca65 import NACA65Spec
from .models_v2 import GeometrySpecV2
from .naca65 import generate_naca65


def generate_record(family: str, spec: dict[str, Any]) -> dict[str, Any]:
    if family == "custom":
        geometry = generate_profile(GeometrySpecV2.model_validate(spec))
        identifier = geometry.geometry_id
        suction, pressure, camber, _ = geometry.arrays()
        canonical = geometry.spec.model_dump(mode="json")
        name = "自定义叶型"
    elif family == "naca65":
        geometry = generate_naca65(NACA65Spec.model_validate(spec))
        identifier = geometry.native_id
        suction = np.asarray(geometry.normalized_suction)
        pressure = np.asarray(geometry.normalized_pressure)
        camber = np.asarray(geometry.camber)
        canonical = geometry.spec.model_dump(mode="json")
        name = geometry.designation
    elif family == "double_arc":
        parsed = ArcProfileSpec.model_validate(spec)
        if parsed.mean_line.family.value != "double":
            raise ValueError("标准叶型工作台仅支持双圆弧")
        geometry = generate_arc_profile(parsed)
        identifier = geometry.profile_id
        suction, pressure, camber, _ = geometry.arrays()
        canonical = geometry.spec.model_dump(mode="json")
        name = "双圆弧叶型"
    else:
        raise ValueError("不支持的叶型系列")
    return {
        "schema_version": "native-profile-1.0", "geometry_id": identifier,
        "family": family, "name": name, "spec": canonical,
        "suction": suction.tolist(), "pressure": pressure.tolist(), "camber": camber.tolist(),
        "coordinate_convention": "chord-local:x-le-to-te,y-to-suction",
        "native_geometry": geometry.model_dump(mode="json"),
        "created_at": datetime.now(UTC).isoformat(),
    }


def coordinate_text(record: dict[str, Any], format: str) -> str:
    stream = io.StringIO()
    if format == "csv":
        writer = csv.writer(stream)
        writer.writerow(["side", "point", *( ["x", "y"] if record.get("family") == "imported"
                                             else ["x_over_c", "y_over_c"])])
        for side in ("suction", "pressure"):
            for index, point in enumerate(record[side]):
                writer.writerow([side, index, *point])
    elif format == "dat":
        # A closed contour, not an IGG SECTIONAL file. Retain finite TE gap.
        points = list(reversed(record["suction"])) + record["pressure"][1:]
        if not np.allclose(points[0], points[-1], rtol=0, atol=1e-14):
            points.append(points[0])
        stream.write(record["geometry_id"] + "\n")
        for x, y in points:
            stream.write(f"{x:.16g} {y:.16g}\n")
    else:
        raise ValueError("坐标格式仅支持 csv / dat")
    return stream.getvalue()


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

    def save(self, record: dict[str, Any], *, write_files: bool = True) -> None:
        with self._lock:
            self._save(record, write_files=write_files)

    def _save(self, record: dict[str, Any], *, write_files: bool) -> None:
        record = self.get(record["geometry_id"]) or record
        files = {}
        if write_files:
            for format in ("csv", "dat"):
                files[self.root / f"{record['geometry_id']}.{format}"] = coordinate_text(
                    record, format)
            files[self.root / f"{record['geometry_id']}.json"] = json.dumps(
                record, ensure_ascii=False, indent=2, allow_nan=False)
            for path, content in files.items():
                if path.is_symlink() or (path.exists() and
                        path.read_text(encoding="utf-8") != content.replace("\r\n", "\n")):
                    raise ValueError(f"已有文件 {path.name} 内容不同，不覆盖用户文件")
        with sqlite3.connect(self.path, timeout=30) as connection:
            if record.get("source_file"):
                connection.execute(
                    "DELETE FROM profiles WHERE family = 'imported' AND "
                    "json_extract(payload, '$.source_file') = ? AND geometry_id != ?",
                    (record["source_file"], record["geometry_id"]))
            connection.execute(
                "INSERT OR IGNORE INTO profiles VALUES (?, ?, ?, ?)",
                (record["geometry_id"], record["family"], record["name"],
                 json.dumps(record, ensure_ascii=False, allow_nan=False)),
            )
        for path, content in files.items():
            if not path.exists():
                with path.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(content)

    def get(self, geometry_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT payload FROM profiles WHERE geometry_id = ?", (geometry_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list(self, search: str = "", family: str = "") -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT payload FROM profiles WHERE "
                "(? = '' OR family = ?) AND "
                "(? = '' OR instr(geometry_id, ?) > 0 OR instr(name, ?) > 0) "
                "ORDER BY rowid DESC", (family, family, search, search, search)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def summaries(self, search: str = "", family: str = "") -> list[dict[str, Any]]:
        with sqlite3.connect(self.path) as connection:
            rows = connection.execute(
                "SELECT geometry_id, family, name FROM profiles WHERE "
                "(? = '' OR family = ?) AND "
                "(? = '' OR instr(geometry_id, ?) > 0 OR instr(name, ?) > 0) "
                "ORDER BY rowid DESC", (family, family, search, search, search)
            ).fetchall()
        return [{"geometry_id": row[0], "family": row[1], "name": row[2],
                 "source": "profile-library"} for row in rows]

    def export_dataset(self) -> bytes:
        records = self.list()
        sink = io.BytesIO()
        with ZipFile(sink, "w", ZIP_DEFLATED) as archive:
            archive.writestr("profiles.json", json.dumps(records, ensure_ascii=False))
            archive.writestr("manifest.json", json.dumps({
                "schema_version": "native-profile-1.0", "count": len(records),
                "coordinate_units": "per-profile" if any(
                    record["family"] == "imported" for record in records) else "chord-normalized",
                "contains_native_contracts": all(record["family"] != "imported"
                                                 for record in records),
                "profiles": [{"geometry_id": record["geometry_id"],
                              "family": record["family"],
                              "coordinate_convention": record.get("coordinate_convention", ""),
                              "native_sha256": hashlib.sha256(json.dumps(
                                  record["native_geometry"], sort_keys=True,
                                  ensure_ascii=False).encode("utf-8")).hexdigest()}
                             for record in records],
            }, indent=2))
            if records:
                table = pa.Table.from_pylist([{
                    "geometry_id": r["geometry_id"], "family": r["family"],
                    "spec_json": json.dumps(r["spec"]),
                    "suction": r["suction"], "pressure": r["pressure"],
                } for r in records])
                parquet = io.BytesIO()
                pq.write_table(table, parquet)
                archive.writestr("profiles.parquet", parquet.getvalue())
            for record in records:
                for format in ("csv", "dat"):
                    archive.writestr(f"coordinates/{record['geometry_id']}.{format}",
                                     coordinate_text(record, format))
        return sink.getvalue()
