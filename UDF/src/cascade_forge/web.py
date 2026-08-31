from __future__ import annotations

import csv
import io
import os
from pathlib import Path
from typing import Any

import numpy as np
import plotly.graph_objects as go
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from plotly.offline import get_plotlyjs
from pydantic import BaseModel, Field

from .audit_v2 import audit_dataset_v2
from .blade3d import build_blade
from .features import curve_curvature, extract_features
from .fitting_v2 import fit_anchors_to_dataset
from .geometry2d import generate_profile
from .geometry_v2 import resolve_geometry_spec
from .geomturbo import export_geomturbo
from .jobs import JobManager
from .models import CascadeContext, GeometrySpec, SectionSchedule
from .models_v2 import GeometrySpecV2
from .sampling import generate_dataset
from .sampling_v2 import generate_dataset_v2
from .storage import DatasetStore
from .storage_v2 import DatasetStoreV2
from .validation import validate_profile
from .workspace import WorkspaceStore

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PACKAGE_ROOT / "configs" / "geometry-v1.0.0.yaml"
DEFAULT_CONFIG_V2 = PACKAGE_ROOT / "configs" / "geometry-v2.0.0.yaml"
DEFAULT_ANCHORS = PACKAGE_ROOT / "data" / "raw" / "nasa120859"
TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"
STATIC_ROOT = Path(__file__).resolve().parent / "static"


class BatchRequest(BaseModel):
    target_valid: int = Field(gt=0, le=100000)
    config_path: str | None = None


class BatchRequestV2(BatchRequest):
    workers: int = Field(default=1, ge=1, le=64)


class WorkspaceDesignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list)
    spec: GeometrySpecV2


class CompareRequestV2(BaseModel):
    geometry_ids: list[str] = Field(min_length=2, max_length=12)


class CascadeRequestV2(BaseModel):
    geometry_id: str
    solidity: float = Field(ge=0.7, le=2.2)


class AnchorFitRequestV2(BaseModel):
    raw_root: str | None = None


def create_app(
    dataset_root: str | Path,
    *,
    v2_dataset_root: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> FastAPI:
    dataset_root = Path(dataset_root).resolve()
    manifest_path = dataset_root / "dataset_manifest.json"
    is_v2_root = False
    if manifest_path.exists():
        import json

        is_v2_root = (
            str(json.loads(manifest_path.read_text(encoding="utf-8")).get("schema_version"))
            == "2.0.0"
        )
    if is_v2_root:
        v1_root = dataset_root.parent / "geometry-v1.0.0"
        resolved_v2_root = dataset_root
    else:
        v1_root = dataset_root
        resolved_v2_root = (
            Path(v2_dataset_root).resolve()
            if v2_dataset_root
            else dataset_root.parent / "geometry-v2.0.0"
        )
    store = DatasetStore(v1_root)
    store_v2 = DatasetStoreV2(resolved_v2_root)
    workspace = WorkspaceStore(
        Path(workspace_root).resolve()
        if workspace_root
        else resolved_v2_root.parent / "workspace-v2"
    )
    jobs = JobManager(store)
    jobs_v2 = JobManager(store_v2)
    templates = Jinja2Templates(directory=str(TEMPLATE_ROOT))
    application = FastAPI(title="Cascade Forge", version="2.0.0")
    application.state.store = store
    application.state.jobs = jobs
    application.state.store_v2 = store_v2
    application.state.workspace = workspace
    application.state.jobs_v2 = jobs_v2
    application.state.v2_dataset_root = resolved_v2_root
    application.state.dataset_root = dataset_root
    application.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")

    @application.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/overview.html",
            _page_context("overview", store_v2, workspace),
        )

    @application.get("/studio", response_class=HTMLResponse)
    def studio_page(request: Request) -> HTMLResponse:
        context = _page_context("studio", store_v2, workspace)
        context["default_spec_v2"] = _default_spec_v2().model_dump(mode="json")
        return templates.TemplateResponse(request, "pages/studio.html", context)

    @application.get("/database", response_class=HTMLResponse)
    def database_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/database.html",
            _page_context("database", store_v2, workspace),
        )

    @application.get("/compare", response_class=HTMLResponse)
    def compare_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/compare.html",
            _page_context("compare", store_v2, workspace),
        )

    @application.get("/cascade", response_class=HTMLResponse)
    def cascade_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/cascade.html",
            _page_context("cascade", store_v2, workspace),
        )

    @application.get("/anchors", response_class=HTMLResponse)
    def anchors_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/anchors.html",
            _page_context("anchors", store_v2, workspace),
        )

    @application.get("/blade", response_class=HTMLResponse)
    def blade_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/blade.html",
            _page_context("blade", store_v2, workspace),
        )

    @application.get("/datasets", response_class=HTMLResponse)
    def datasets_page(request: Request) -> HTMLResponse:
        context = _page_context("datasets", store_v2, workspace)
        context["default_workers"] = min(8, max(1, (os.cpu_count() or 2) - 1))
        return templates.TemplateResponse(request, "pages/datasets.html", context)

    @application.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/audit.html",
            _page_context("audit", store_v2, workspace),
        )

    @application.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/jobs.html",
            _page_context("jobs", store_v2, workspace),
        )

    @application.get("/assets/plotly.min.js")
    def plotly_javascript() -> Response:
        return Response(get_plotlyjs(), media_type="application/javascript")

    @application.get("/api/v1/geometries")
    def list_geometries(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        origin: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        return {
            "items": store.list_geometries(
                limit=limit, offset=offset, origin=origin, status=status
            ),
            "limit": limit,
            "offset": offset,
            "total": store.count(),
        }

    @application.get("/api/v1/geometries/{geometry_id}")
    def get_geometry(geometry_id: str) -> dict[str, Any]:
        geometry = store.get_geometry(geometry_id)
        if geometry is None:
            raise HTTPException(404, f"找不到 {geometry_id}")
        return {
            "geometry": geometry.model_dump(mode="json"),
            "features": extract_features(geometry).model_dump(mode="json"),
            "validation": validate_profile(geometry).model_dump(mode="json"),
        }

    @application.post("/api/v1/preview")
    def preview(spec: GeometrySpec) -> dict[str, Any]:
        geometry = generate_profile(spec)
        return {
            "geometry": geometry.model_dump(mode="json"),
            "features": extract_features(geometry).model_dump(mode="json"),
            "validation": validate_profile(geometry).model_dump(mode="json"),
        }

    @application.post("/api/v1/batches", status_code=202)
    def start_batch(body: BatchRequest) -> dict[str, str]:
        config_path = Path(body.config_path).resolve() if body.config_path else DEFAULT_CONFIG

        def run() -> dict[str, Any]:
            manifest = generate_dataset(config_path, dataset_root, target_valid=body.target_valid)
            return manifest.model_dump(mode="json")

        job_id = jobs.submit("generate_dataset", body.model_dump(mode="json"), run)
        return {"job_id": job_id, "status": "queued"}

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
        camber_family: str | None = None,
        thickness_family: str | None = None,
        status: str | None = None,
        origin: str | None = None,
        sort: str = "geometry_id",
        descending: bool = False,
    ) -> dict[str, Any]:
        try:
            items = store_v2.list_geometries(
                limit=limit,
                offset=offset,
                camber_family=camber_family,
                thickness_family=thickness_family,
                status=status,
                origin=origin,
                sort=sort,
                descending=descending,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "total": store_v2.count(),
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
        from .models_v2 import AirfoilGeometryV2

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

    @application.post("/api/v2/batches", status_code=202)
    def start_batch_v2(body: BatchRequestV2) -> dict[str, str]:
        config_path = Path(body.config_path).resolve() if body.config_path else DEFAULT_CONFIG_V2

        def run() -> dict[str, Any]:
            return generate_dataset_v2(
                config_path,
                resolved_v2_root,
                target_valid=body.target_valid,
                workers=body.workers,
            ).model_dump(mode="json")

        job_id = jobs_v2.submit("generate_dataset_v2", body.model_dump(mode="json"), run)
        return {"job_id": job_id, "status": "queued"}

    @application.post("/api/v2/anchor-fits", status_code=202)
    def start_anchor_fits_v2(body: AnchorFitRequestV2) -> dict[str, str]:
        raw_root = Path(body.raw_root).resolve() if body.raw_root else DEFAULT_ANCHORS

        def run() -> dict[str, Any]:
            return fit_anchors_to_dataset(raw_root, resolved_v2_root)

        job_id = jobs_v2.submit("fit_anchors_v2", body.model_dump(mode="json"), run)
        return {"job_id": job_id, "status": "queued"}

    @application.get("/api/v2/anchor-fits")
    def list_anchor_fits_v2(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        status: str | None = None,
    ) -> dict[str, Any]:
        return {
            "items": store_v2.list_anchor_fits(limit=limit, offset=offset, status=status),
            "total": store_v2.fit_count(),
            "limit": limit,
            "offset": offset,
        }

    @application.get("/api/v2/audit")
    def audit_v2(rebuild_sample: int = Query(8, ge=0, le=256)) -> dict[str, Any]:
        return audit_dataset_v2(
            resolved_v2_root,
            rebuild_sample=rebuild_sample,
            require_anchors=False,
        )

    @application.get("/api/v2/jobs")
    def list_jobs_v2(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
        return {"items": store_v2.list_jobs(limit=limit)}

    @application.get("/api/v2/jobs/{job_id}")
    def get_job_v2(job_id: str) -> dict[str, Any]:
        job = store_v2.get_job(job_id)
        if job is None:
            raise HTTPException(404, f"找不到 {job_id}")
        return job

    @application.post("/api/v2/blades", status_code=202)
    def start_blade_v2(schedule: SectionSchedule) -> dict[str, str]:
        def run() -> dict[str, Any]:
            blade = build_blade(schedule, store_v2.get_geometry)
            output = resolved_v2_root / "artifacts" / blade.blade_id / f"{schedule.name}.geomTurbo"
            path = export_geomturbo(blade, output)
            return {
                "blade_id": blade.blade_id,
                "artifact": path.relative_to(resolved_v2_root / "artifacts").as_posix(),
            }

        job_id = jobs_v2.submit("build_blade_v2", schedule.model_dump(mode="json"), run)
        return {"job_id": job_id, "status": "queued"}

    @application.post("/api/v2/blades/preview")
    def preview_blade_v2(schedule: SectionSchedule) -> dict[str, Any]:
        try:
            blade = build_blade(schedule, store_v2.get_geometry)
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return blade.model_dump(mode="json")

    @application.get("/api/v2/artifacts/{artifact_path:path}")
    def download_artifact_v2(artifact_path: str) -> FileResponse:
        root = (resolved_v2_root / "artifacts").resolve()
        path = (root / artifact_path).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "制品不存在")
        return FileResponse(path, filename=path.name)

    @application.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, f"找不到 {job_id}")
        return job

    @application.post("/api/v1/blades", status_code=202)
    def start_blade(schedule: SectionSchedule) -> dict[str, str]:
        def run() -> dict[str, Any]:
            blade = build_blade(schedule, store.get_geometry)
            output = dataset_root / "artifacts" / blade.blade_id / f"{schedule.name}.geomTurbo"
            path = export_geomturbo(blade, output)
            return {"blade_id": blade.blade_id, "artifact": str(path)}

        job_id = jobs.submit("build_blade", schedule.model_dump(mode="json"), run)
        return {"job_id": job_id, "status": "queued"}

    @application.post("/ui/preview", response_class=HTMLResponse)
    def preview_partial(
        request: Request,
        inlet_metal_angle_deg: float = Form(...),
        turning_angle_deg: float = Form(...),
        max_thickness_ratio: float = Form(...),
        max_thickness_x_ratio: float = Form(...),
        leading_edge_radius_ratio: float = Form(...),
        trailing_edge_thickness_ratio: float = Form(...),
        trailing_edge_half_wedge_deg: float = Form(...),
        quarter_angle_bias_deg: float = Form(0.0),
        three_quarter_angle_bias_deg: float = Form(0.0),
        fore_shape_factor: float = Form(1.0),
        aft_shape_factor: float = Form(1.0),
    ) -> HTMLResponse:
        try:
            spec = GeometrySpec(**locals_without_request(locals()))
            geometry = generate_profile(spec)
            report = validate_profile(geometry)
            figure = _profile_figure(geometry)
            return templates.TemplateResponse(
                request,
                "partials/preview.html",
                {
                    "plot_json": figure.to_json(),
                    "geometry": geometry,
                    "features": extract_features(geometry),
                    "report": report,
                },
            )
        except Exception as exc:
            return templates.TemplateResponse(
                request, "partials/error.html", {"message": str(exc)}, status_code=422
            )

    @application.get("/ui/geometries", response_class=HTMLResponse)
    def geometries_partial(request: Request, limit: int = 50, offset: int = 0) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "partials/geometries.html",
            {
                "items": store.list_geometries(limit=limit, offset=offset),
                "offset": offset,
                "limit": limit,
            },
        )

    @application.get("/ui/jobs/{job_id}", response_class=HTMLResponse)
    def job_partial(request: Request, job_id: str) -> HTMLResponse:
        job = store.get_job(job_id)
        if job is None:
            raise HTTPException(404, f"找不到 {job_id}")
        return templates.TemplateResponse(request, "partials/job.html", {"job": job})

    @application.get("/ui/compare", response_class=HTMLResponse)
    def compare_partial(request: Request, left: str, right: str) -> HTMLResponse:
        left_geometry = store.get_geometry(left)
        right_geometry = store.get_geometry(right)
        if left_geometry is None or right_geometry is None:
            return templates.TemplateResponse(
                request,
                "partials/error.html",
                {"message": "比较所需的 Geometry_ID 不存在"},
                status_code=404,
            )
        figure = _comparison_figure(left_geometry, right_geometry)
        return templates.TemplateResponse(
            request,
            "partials/plot.html",
            {"plot_id": "compare-plot", "plot_json": figure.to_json()},
        )

    @application.post("/ui/blade-preview", response_class=HTMLResponse)
    def blade_preview_partial(request: Request, schedule_json: str = Form(...)) -> HTMLResponse:
        try:
            schedule = SectionSchedule.model_validate_json(schedule_json)
            blade = build_blade(schedule, store.get_geometry)
            figure = _blade_figure(blade)
            return templates.TemplateResponse(
                request,
                "partials/plot.html",
                {"plot_id": "blade-plot", "plot_json": figure.to_json()},
            )
        except Exception as exc:
            return templates.TemplateResponse(
                request, "partials/error.html", {"message": str(exc)}, status_code=422
            )

    @application.get("/health")
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "schema_version": "2.0.0",
                "v1": {"total": store.count()},
                "v2": {"total": store_v2.count()},
            }
        )

    return application


def _default_spec() -> GeometrySpec:
    return GeometrySpec(
        inlet_metal_angle_deg=58,
        turning_angle_deg=32,
        quarter_angle_bias_deg=0,
        three_quarter_angle_bias_deg=0,
        max_thickness_ratio=0.10,
        max_thickness_x_ratio=0.40,
        leading_edge_radius_ratio=0.004,
        trailing_edge_thickness_ratio=0.008,
        trailing_edge_half_wedge_deg=8,
        fore_shape_factor=1,
        aft_shape_factor=1,
    )


def _default_spec_v2() -> GeometrySpecV2:
    from .models_v2 import GeometryIntentV2

    return GeometrySpecV2(
        intent=GeometryIntentV2(
            inlet_metal_angle_deg=55.0,
            turning_angle_deg=25.0,
            max_thickness_ratio=0.10,
            max_thickness_x_ratio=0.42,
            leading_edge_radius_ratio=0.008,
            trailing_edge_radius_ratio=0.002,
            trailing_edge_half_wedge_deg=8.0,
        )
    )


def _page_context(page: str, store: DatasetStoreV2, workspace: WorkspaceStore) -> dict[str, Any]:
    return {
        "page": page,
        "v2_generated": store.count("generated"),
        "v2_anchors": store.anchor_count(),
        "v2_fits": store.fit_count(),
        "v2_rejected": store.rejected_count(),
        "v2_groups": store.complete_group_count(),
        "workspace_count": len(workspace.list_designs(limit=1000)),
    }


def _geometry_payload(geometry) -> dict[str, Any]:
    suction, pressure, camber, thickness = geometry.arrays()
    suction_curvature, suction_s = curve_curvature(suction)
    pressure_curvature, pressure_s = curve_curvature(pressure)
    camber_delta = np.gradient(camber, axis=0, edge_order=2)
    angle = np.degrees(np.arctan2(camber_delta[:, 1], camber_delta[:, 0]))
    return {
        "geometry": geometry.model_dump(mode="json"),
        "features": extract_features(geometry).model_dump(mode="json"),
        "validation": validate_profile(geometry).model_dump(mode="json"),
        "analysis": {
            "camber_x": camber[:, 0].tolist(),
            "thickness": thickness.tolist(),
            "suction_s": suction_s.tolist(),
            "pressure_s": pressure_s.tolist(),
            "suction_curvature": suction_curvature.tolist(),
            "pressure_curvature": pressure_curvature.tolist(),
            "suction_curvature_gradient": np.gradient(
                suction_curvature, suction_s, edge_order=2
            ).tolist(),
            "pressure_curvature_gradient": np.gradient(
                pressure_curvature, pressure_s, edge_order=2
            ).tolist(),
            "angle_deg": angle.tolist(),
        },
    }


def _profile_figure(geometry) -> go.Figure:
    suction, pressure, camber, _ = geometry.arrays()
    figure = go.Figure()
    figure.add_scatter(x=suction[:, 0], y=suction[:, 1], name="吸力面", mode="lines")
    figure.add_scatter(x=pressure[:, 0], y=pressure[:, 1], name="压力面", mode="lines")
    figure.add_scatter(
        x=camber[:, 0], y=camber[:, 1], name="中弧线", mode="lines", line={"dash": "dash"}
    )
    figure.update_layout(
        template="plotly_white",
        margin={"l": 30, "r": 10, "t": 35, "b": 30},
        yaxis={"scaleanchor": "x", "scaleratio": 1},
        title=geometry.geometry_id,
    )
    return figure


def _comparison_figure(left, right) -> go.Figure:
    figure = go.Figure()
    for geometry, dash in ((left, "solid"), (right, "dash")):
        suction, pressure, _, _ = geometry.arrays()
        for side, coordinates in (("SS", suction), ("PS", pressure)):
            figure.add_scatter(
                x=coordinates[:, 0],
                y=coordinates[:, 1],
                name=f"{geometry.geometry_id} {side}",
                mode="lines",
                line={"dash": dash},
            )
    figure.update_layout(
        template="plotly_white",
        yaxis={"scaleanchor": "x", "scaleratio": 1},
        margin={"l": 30, "r": 10, "t": 35, "b": 30},
    )
    return figure


def _blade_figure(blade) -> go.Figure:
    figure = go.Figure()
    for index, section in enumerate(blade.sections):
        for side, coordinates in (
            ("SS", section.suction_xyz),
            ("PS", section.pressure_xyz),
        ):
            points = list(zip(*coordinates, strict=True))
            figure.add_scatter3d(
                x=points[0],
                y=points[1],
                z=points[2],
                mode="lines",
                name=f"S{index + 1}-{side}",
                showlegend=index in {0, len(blade.sections) - 1},
            )
    figure.update_layout(
        template="plotly_white",
        scene={"aspectmode": "data"},
        margin={"l": 0, "r": 0, "t": 35, "b": 0},
        title=blade.blade_id,
    )
    return figure


def locals_without_request(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in values.items()
        if key not in {"request", "spec", "geometry", "report", "figure", "exc"}
    }
