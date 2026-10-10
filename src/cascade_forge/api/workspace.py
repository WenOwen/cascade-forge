from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse

from cascade_forge.api.schemas import (
    WorkspaceDesignRequest,
)
from cascade_forge.domain.geometry import generate_profile


def register_routes(application):
    state = application.state
    workspace = state.workspace

    @application.post("/api/v2/workspace/designs", status_code=201)
    def save_workspace_design(body: WorkspaceDesignRequest) -> dict[str, Any]:
        geometry = generate_profile(body.spec)
        design_id = workspace.save_design(body.name, geometry, tags=body.tags)
        return {"design_id": design_id, "geometry_id": geometry.geometry_id}

    @application.get("/api/v2/workspace/designs")
    def list_workspace_designs(
        limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)
    ) -> dict[str, Any]:
        return {
            "items": workspace.list_designs(limit=limit, offset=offset),
            "limit": limit,
            "offset": offset,
        }

    @application.get("/api/v2/workspace/designs/{design_id}")
    def get_workspace_design(design_id: str) -> dict[str, Any]:
        record = workspace.get_design(design_id)
        if record is None:
            raise HTTPException(404, f"找不到 {design_id}")
        return {
            **record,
            "spec": record["spec"].model_dump(mode="json"),
            "geometry": record["geometry"].model_dump(mode="json"),
        }

    @application.post("/api/v2/workspace/import", status_code=201)
    def import_workspace_design(payload: dict[str, Any]) -> dict[str, str]:
        geometry = payload.get("geometry")
        if geometry is None:
            raise HTTPException(422, "缺少 geometry")
        from cascade_forge.domain.models import AirfoilGeometryV2

        parsed = AirfoilGeometryV2.model_validate(geometry)
        design_id = workspace.save_design(
            str(payload.get("name", "导入设计")),
            parsed,
            tags=list(payload.get("tags", [])),
        )
        return {"design_id": design_id}

    @application.get("/api/v2/workspace/designs/{design_id}/export")
    def export_workspace_design(design_id: str) -> JSONResponse:
        record = workspace.get_design(design_id)
        if record is None:
            raise HTTPException(404, f"找不到 {design_id}")
        return JSONResponse(
            {
                "workspace_schema": "1.0.0",
                "name": record["name"],
                "revision": record["revision"],
                "tags": record["tags"],
                "spec": record["spec"].model_dump(mode="json"),
                "geometry": record["geometry"].model_dump(mode="json"),
            },
            headers={"Content-Disposition": f'attachment; filename="{design_id}.json"'},
        )
