from __future__ import annotations

from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from plotly.offline import get_plotlyjs
from pydantic import BaseModel, Field

from .blade3d import build_blade
from .features import extract_features
from .geometry2d import generate_profile
from .geomturbo import export_geomturbo
from .jobs import JobManager
from .models import GeometrySpec, SectionSchedule
from .sampling import generate_dataset
from .storage import DatasetStore
from .validation import validate_profile

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PACKAGE_ROOT / "configs" / "geometry-v1.0.0.yaml"
TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"
STATIC_ROOT = Path(__file__).resolve().parent / "static"


class BatchRequest(BaseModel):
    target_valid: int = Field(gt=0, le=100000)
    config_path: str | None = None


def create_app(dataset_root: str | Path) -> FastAPI:
    dataset_root = Path(dataset_root).resolve()
    store = DatasetStore(dataset_root)
    jobs = JobManager(store)
    templates = Jinja2Templates(directory=str(TEMPLATE_ROOT))
    application = FastAPI(title="Cascade Forge", version="1.0.0")
    application.state.store = store
    application.state.jobs = jobs
    application.state.dataset_root = dataset_root
    application.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")

    @application.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "total": store.count(),
                "generated": store.count("generated"),
                "anchors": store.count("anchor"),
                "rejected": store.rejected_count(),
                "default_spec": _default_spec().model_dump(mode="json"),
            },
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
        return JSONResponse({"status": "ok", "schema_version": "1.0.0"})

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
