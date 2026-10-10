"""Small product API for native generation, datasets and exports."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .blade3d import build_blade
from .database_folders import DEFAULT_DATABASE, DatabaseFolders, scan_dat
from .geomturbo import export_geomturbo
from .models import SectionSchedule
from .profile_library import ProfileLibrary, coordinate_text, generate_record


class ProfileRequest(BaseModel):
    family: Literal["custom", "naca65", "double_arc"]
    spec: dict[str, Any]
    database: str = DEFAULT_DATABASE


class DatabaseRequest(BaseModel):
    name: str


class SweepRequest(ProfileRequest):
    parameter: str
    minimum: float = Field(allow_inf_nan=False)
    maximum: float = Field(allow_inf_nan=False)
    count: int = Field(default=10, ge=1, le=100)


SWEEP_PARAMETERS = {
    "custom": {"max_thickness_ratio", "max_thickness_x_ratio", "inlet_metal_angle_deg",
               "outlet_metal_angle_deg", "leading_edge_radius_ratio"},
    "naca65": {"thickness_ratio", "design_lift_coefficient"},
    "double_arc": {"max_thickness_ratio", "max_thickness_station", "inlet_metal_angle_deg",
                   "outlet_metal_angle_deg", "trailing_edge_half_wedge_deg"},
}


def register_workbench(application, templates) -> None:
    state = application.state
    folders = DatabaseFolders(
        state.profile_database_root,
        state.v2_dataset_root.parent / "_archive" / "profile-library-v1",
    )
    state.database_folders = folders
    state.profile_library = folders.library(DEFAULT_DATABASE)
    router = APIRouter(prefix="/api/workbench")

    def get_library(name: str) -> ProfileLibrary:
        try:
            return folders.library(name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/databases")
    def list_databases():
        return {"root": str(folders.root), "items": folders.list(),
                "default": DEFAULT_DATABASE}

    @router.post("/databases", status_code=201)
    def create_database(body: DatabaseRequest):
        try:
            library = folders.library(body.name, create=True)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"name": library.root.name, "path": str(library.root)}

    @router.post("/databases/{name}/scan")
    def scan_database(name: str):
        return scan_dat(get_library(name))

    def generate(body: ProfileRequest) -> dict[str, Any]:
        try:
            return generate_record(body.family, body.spec)
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc

    def legacy_record(geometry) -> dict[str, Any]:
        suction, pressure, camber, _ = geometry.arrays()
        return {"geometry_id": geometry.geometry_id, "family": "custom",
                "name": "已有叶型", "spec": geometry.spec.model_dump(mode="json"),
                "suction": suction.tolist(), "pressure": pressure.tolist(),
                "camber": camber.tolist(), "source": "existing"}

    def resolve_record(identifier: str, database: str = DEFAULT_DATABASE) -> dict[str, Any]:
        record = get_library(database).get(identifier)
        if record:
            return record
        if database != DEFAULT_DATABASE:
            raise HTTPException(404, "该数据库中找不到叶型")
        geometry = state.store_v2.get_geometry(identifier)
        if geometry:
            return legacy_record(geometry)
        for design in state.workspace.list_designs(limit=1000):
            if design["geometry_id"] == identifier:
                return legacy_record(state.workspace.get_design(design["design_id"])["geometry"])
        raise HTTPException(404, "找不到该叶型")

    @router.post("/preview")
    def preview(body: ProfileRequest):
        return generate(body)

    @router.post("/profiles", status_code=201)
    def save_profile(body: ProfileRequest):
        record = generate(body)
        library = get_library(body.database)
        try:
            library.save(record)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"geometry_id": record["geometry_id"], "dataset": body.database,
                "path": str(library.root)}

    @router.post("/export/{format}")
    def export_current(format: Literal["csv", "dat"], body: ProfileRequest):
        record = generate(body)
        return coordinate_response(record, format)

    def coordinate_response(record, format):
        return Response(coordinate_text(record, format),
                        media_type="text/csv" if format == "csv" else "text/plain",
                        headers={"Content-Disposition":
                                 f'attachment; filename="{record["geometry_id"]}.{format}"'})

    @router.get("/profiles")
    def list_profiles(search: str = "", family: str = "", database: str = DEFAULT_DATABASE,
                      limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)):
        items = get_library(database).summaries(search, family)
        seen = {r["geometry_id"] for r in items}
        if database == DEFAULT_DATABASE and family in ("", "custom"):
            # Existing large spline datasets stay queryable without loading coordinates.
            total_existing = state.store_v2.count(search=search or None)
            rows = []
            for start in range(0, total_existing, 1000):
                rows.extend(state.store_v2.list_geometries(limit=1000, offset=start,
                                                           search=search or None))
            for row in rows:
                if row["geometry_id"] not in seen:
                    items.append({"geometry_id": row["geometry_id"], "family": "custom",
                                  "name": "已有叶型", "source": "existing"})
                    seen.add(row["geometry_id"])
            for design in state.workspace.list_designs(limit=1000):
                identifier = design["geometry_id"]
                if identifier not in seen and (not search or search in identifier
                                              or search in design["name"]):
                    items.append({"geometry_id": identifier, "family": "custom",
                                  "name": design["name"], "source": "workspace"})
                    seen.add(identifier)
            total = len(items) + max(0, total_existing - len(rows))
        else:
            total = len(items)
        return {"items": items[offset:offset + limit], "total": total}

    @router.get("/profiles/{identifier}")
    def get_profile(identifier: str, database: str = DEFAULT_DATABASE):
        return resolve_record(identifier, database)

    @router.get("/profiles/{identifier}/export/{format}")
    def export_saved(identifier: str, format: Literal["csv", "dat"],
                     database: str = DEFAULT_DATABASE):
        return coordinate_response(resolve_record(identifier, database), format)

    @router.get("/dataset/export")
    def export_dataset(database: str = DEFAULT_DATABASE):
        return Response(get_library(database).export_dataset(), media_type="application/zip",
                        headers={"Content-Disposition":
                                 'attachment; filename="profile-dataset.zip"'})

    @router.post("/batches", status_code=202)
    def start_sweep(body: SweepRequest):
        if body.parameter not in SWEEP_PARAMETERS[body.family]:
            raise HTTPException(422, "不支持该扫描参数")
        if body.minimum > body.maximum:
            raise HTTPException(422, "扫描下限不能高于上限")
        # Validate base contract before creating a background job.
        generate(body)
        library = get_library(body.database)

        def run():
            succeeded, failures = [], []
            for value in np.linspace(body.minimum, body.maximum, body.count):
                spec = deepcopy(body.spec)
                target = spec
                if body.family == "custom":
                    target = spec["intent"]
                elif body.family == "double_arc" and body.parameter in {
                    "inlet_metal_angle_deg", "outlet_metal_angle_deg"
                }:
                    target = spec.setdefault("mean_line", {})
                target[body.parameter] = float(value)
                try:
                    if body.family == "custom":
                        # Rebuild explicit curves from intent rather than reuse stale splines.
                        from .models_v2 import GeometrySpecV2
                        for key in ("camber_curve", "suction_thickness_curve",
                                    "pressure_thickness_curve", "edge"):
                            spec[key] = None
                        spec["authoring_mode"] = "basic"
                        spec = GeometrySpecV2.model_validate(spec).model_dump(mode="json")
                    record = generate_record(body.family, spec)
                    library.save(record)
                    succeeded.append(record["geometry_id"])
                except (ValueError, ArithmeticError) as exc:
                    failures.append({"value": float(value), "error": str(exc)})
            return {"generated": len(succeeded), "requested": body.count,
                    "unique": len(set(succeeded)), "geometry_ids": succeeded,
                    "failures": failures, "dataset": library.root.name}

        identifier = state.jobs_v2.submit("profile_sweep", body.model_dump(mode="json"), run)
        return {"job_id": identifier, "status": "queued"}

    @router.post("/geomturbo")
    def geomturbo(schedule: SectionSchedule, database: str = DEFAULT_DATABASE):
        if schedule.hub_curve_path or schedule.shroud_curve_path:
            raise HTTPException(422, "当前导出仅支持由截面半径定义的轮毂与机匣")
        profiles = [resolve_record(section.geometry_id, database) for section in schedule.sections]
        if any(record["family"] == "imported" for record in profiles):
            raise HTTPException(422, "DAT 原始坐标尚未确认归一化与坐标系，暂不用于三维导出")
        contracts = {(record["family"], record["spec"].get("family", ""),
                      record["spec"].get("points_per_side", 201)) for record in profiles}
        if len(contracts) != 1:
            raise HTTPException(422, "暂不支持跨叶型系列或采样合同的展向插值")
        if len({len(record["suction"]) for record in profiles}) != 1:
            raise HTTPException(422, "展向截面必须具有相同采样点数")
        def resolve(identifier):
            record = resolve_record(identifier, database)
            return SimpleNamespace(geometry_id=identifier, arrays=lambda: (
                np.asarray(record["suction"]), np.asarray(record["pressure"]),
                np.asarray(record["camber"]), np.empty(0)))
        try:
            blade = build_blade(schedule, resolve)
            path = Path(state.v2_dataset_root) / "artifacts" / blade.blade_id / "blade.geomTurbo"
            export_geomturbo(blade, path)
        except (ValueError, KeyError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return FileResponse(path, filename="blade.geomTurbo")

    application.include_router(router)
