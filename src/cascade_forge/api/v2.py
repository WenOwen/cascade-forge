from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response

from cascade_forge.api.schemas import (
    CascadeRequestV2,
    CompareRequestV2,
)
from cascade_forge.domain.features import extract_features
from cascade_forge.domain.geometry import generate_profile, resolve_geometry_spec
from cascade_forge.domain.models import (
    CascadeContext,
    GeometrySpecV2,
)
from cascade_forge.domain.validation import validate_profile
from cascade_forge.workbench.geometry_payload import _geometry_payload


def register_routes(application):
    state = application.state
    store_v2 = state.store_v2
    resolved_v2_root = state.v2_dataset_root

    @application.post("/api/v2/preview")
    def preview_v2(spec: GeometrySpecV2) -> dict[str, Any]:
        try:
            geometry = generate_profile(spec)
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return _geometry_payload(geometry)

    @application.post("/api/v2/curves/resolve")
    def resolve_curves_v2(spec: GeometrySpecV2) -> dict[str, Any]:
        try:
            resolved = resolve_geometry_spec(spec)
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "intent_id": resolved.intent.intent_id,
            "geometry_id": resolved.geometry_id,
            "spec": resolved.model_dump(mode="json"),
            "residuals": resolved.resolution_residuals,
        }

    @application.get("/api/v2/geometries")
    def list_geometries_v2(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        status: str | None = None,
        origin: str | None = None,
        sort: str = "geometry_id",
        descending: bool = False,
        search: str | None = Query(None, max_length=120),
    ) -> dict[str, Any]:
        try:
            items = store_v2.list_geometries(
                limit=limit,
                offset=offset,
                status=status,
                origin=origin,
                sort=sort,
                descending=descending,
                search=search,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "total": store_v2.count(origin=origin, status=status, search=search),
        }

    @application.get("/api/v2/geometries/{geometry_id}")
    def get_geometry_v2(geometry_id: str) -> dict[str, Any]:
        geometry = store_v2.get_geometry(geometry_id)
        if geometry is None:
            raise HTTPException(404, f"找不到 {geometry_id}")
        return _geometry_payload(geometry)

    @application.get("/api/v2/geometries/{geometry_id}/export")
    def export_geometry_v2(geometry_id: str, format: str = "csv") -> Response:
        geometry = store_v2.get_geometry(geometry_id)
        if geometry is None:
            raise HTTPException(404, f"找不到 {geometry_id}")
        if format == "json":
            return JSONResponse(
                geometry.model_dump(mode="json"),
                headers={"Content-Disposition": f'attachment; filename="{geometry_id}.json"'},
            )
        if format != "csv":
            raise HTTPException(422, "format 仅支持 csv 或 json")
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["side", "point", "x_over_c", "y_over_c"])
        suction, pressure, _, _ = geometry.arrays()
        for side, coordinates in (("suction", suction), ("pressure", pressure)):
            for index, coordinate in enumerate(coordinates):
                writer.writerow([side, index, *coordinate])
        return Response(
            stream.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{geometry_id}.csv"'},
        )

    @application.get("/api/v2/dataset/manifest")
    def get_manifest_v2() -> FileResponse:
        path = resolved_v2_root / "dataset_manifest.json"
        if not path.is_file():
            raise HTTPException(404, "v2 manifest 尚未生成")
        return FileResponse(path, filename=path.name)

    @application.post("/api/v2/compare")
    def compare_v2(body: CompareRequestV2) -> dict[str, Any]:
        geometries = []
        for geometry_id in body.geometry_ids:
            geometry = store_v2.get_geometry(geometry_id)
            if geometry is None:
                raise HTTPException(404, f"找不到 {geometry_id}")
            geometries.append(geometry)
        features = [extract_features(item) for item in geometries]
        baseline = features[0]
        return {
            "items": [item.model_dump(mode="json") for item in geometries],
            "features": [item.model_dump(mode="json") for item in features],
            "deltas_from_first": [
                {
                    "geometry_id": item.geometry_id,
                    "max_thickness_ratio": item.max_thickness_ratio - baseline.max_thickness_ratio,
                    "max_thickness_x_ratio": item.max_thickness_x_ratio
                    - baseline.max_thickness_x_ratio,
                    "max_camber_ratio": item.max_camber_ratio - baseline.max_camber_ratio,
                }
                for item in features[1:]
            ],
        }

    @application.post("/api/v2/cascade")
    def cascade_v2(body: CascadeRequestV2) -> dict[str, Any]:
        geometry = store_v2.get_geometry(body.geometry_id)
        if geometry is None:
            raise HTTPException(404, f"找不到 {body.geometry_id}")
        report = validate_profile(geometry, CascadeContext(solidity=body.solidity))
        suction, pressure, _, _ = geometry.arrays()
        pitch = 1.0 / body.solidity
        return {
            "geometry_id": body.geometry_id,
            "solidity": body.solidity,
            "pitch_ratio": pitch,
            "validation": report.model_dump(mode="json"),
            "passages": [
                {
                    "offset": offset,
                    "suction": (suction + [0.0, offset * pitch]).tolist(),
                    "pressure": (pressure + [0.0, offset * pitch]).tolist(),
                }
                for offset in (-1, 0, 1)
            ],
        }

    @application.get("/api/v2/artifacts/{artifact_path:path}")
    def download_artifact_v2(artifact_path: str) -> FileResponse:
        root = (resolved_v2_root / "artifacts").resolve()
        path = (root / artifact_path).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "制品不存在")
        return FileResponse(path, filename=path.name)
