from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from .models_v2 import AirfoilGeometryV2, GeometrySpecV2


class WorkspaceStore:
    """与正式数据集物理隔离的可编辑设计工作区。"""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = self.root / "artifacts"
        self.artifact_dir.mkdir(exist_ok=True)
        self.catalog_path = self.root / "workspace.duckdb"
        with duckdb.connect(str(self.catalog_path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS designs (
                    design_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    geometry_id VARCHAR NOT NULL,
                    revision INTEGER NOT NULL,
                    spec_json VARCHAR NOT NULL,
                    geometry_json VARCHAR NOT NULL,
                    tags_json VARCHAR NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )

    def save_design(
        self,
        name: str,
        geometry: AirfoilGeometryV2,
        *,
        tags: list[str] | None = None,
    ) -> str:
        with duckdb.connect(str(self.catalog_path)) as connection:
            revision = int(
                connection.execute(
                    "SELECT coalesce(max(revision), 0) + 1 FROM designs WHERE name = ?",
                    [name],
                ).fetchone()[0]
            )
            design_id = "design_" + uuid.uuid4().hex
            workspace_geometry = geometry.model_copy(update={"origin": "workspace"})
            connection.execute(
                "INSERT INTO designs VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    design_id,
                    name,
                    geometry.geometry_id,
                    revision,
                    geometry.spec.model_dump_json(),
                    workspace_geometry.model_dump_json(),
                    json.dumps(tags or [], ensure_ascii=False),
                    datetime.now(UTC),
                ],
            )
        return design_id

    def get_design(self, design_id: str) -> dict[str, Any] | None:
        with duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute("SELECT * FROM designs WHERE design_id = ?", [design_id])
            row = result.fetchone()
            if row is None:
                return None
            record = dict(zip([column[0] for column in result.description], row, strict=True))
        record["spec"] = GeometrySpecV2.model_validate_json(record.pop("spec_json"))
        record["geometry"] = AirfoilGeometryV2.model_validate_json(record.pop("geometry_json"))
        record["tags"] = json.loads(record.pop("tags_json"))
        record["created_at"] = str(record["created_at"])
        return record

    def list_designs(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        with duckdb.connect(str(self.catalog_path)) as connection:
            result = connection.execute(
                """
                SELECT design_id, name, geometry_id, revision, tags_json, created_at
                FROM designs ORDER BY created_at DESC LIMIT ? OFFSET ?
                """,
                [max(1, min(limit, 1000)), max(0, offset)],
            )
            columns = [column[0] for column in result.description]
            rows = [dict(zip(columns, row, strict=True)) for row in result.fetchall()]
        for row in rows:
            row["tags"] = json.loads(row.pop("tags_json"))
            row["created_at"] = str(row["created_at"])
        return rows

    def export_design(self, design_id: str, destination: str | Path) -> Path:
        record = self.get_design(design_id)
        if record is None:
            raise KeyError(design_id)
        path = Path(destination).resolve()
        payload = {
            "workspace_schema": "1.0.0",
            "design_id": design_id,
            "name": record["name"],
            "revision": record["revision"],
            "tags": record["tags"],
            "spec": record["spec"].model_dump(mode="json"),
            "geometry": record["geometry"].model_dump(mode="json"),
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def import_design(self, source: str | Path) -> str:
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        geometry = AirfoilGeometryV2.model_validate(payload["geometry"])
        return self.save_design(
            payload.get("name", "导入设计"),
            geometry,
            tags=list(payload.get("tags", [])),
        )
