from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from plotly.offline import get_plotlyjs

from cascade_forge.config import PACKAGE_ROOT, STATIC_ROOT, TEMPLATE_ROOT
from cascade_forge.domain.jobs import JobManager
from cascade_forge.storage.dataset import DatasetStoreV2
from cascade_forge.storage.workspace import WorkspaceStore


def create_app(
    dataset_root: str | Path,
    *,
    workspace_root: str | Path | None = None,
    profile_database_root: str | Path | None = None,
) -> FastAPI:
    dataset_root = Path(dataset_root).resolve()
    resolved_v2_root = dataset_root
    store_v2 = DatasetStoreV2(resolved_v2_root)
    workspace = WorkspaceStore(
        Path(workspace_root).resolve()
        if workspace_root
        else resolved_v2_root.parent / "workspace-v2"
    )
    jobs_v2 = JobManager(store_v2)
    templates = Jinja2Templates(directory=str(TEMPLATE_ROOT))

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            jobs_v2.shutdown()

    application = FastAPI(title="Cascade Forge", version="2.1.0", lifespan=lifespan)
    application.state.templates = templates
    application.state.store_v2 = store_v2
    application.state.workspace = workspace
    application.state.jobs_v2 = jobs_v2
    application.state.v2_dataset_root = resolved_v2_root
    application.state.dataset_root = dataset_root
    application.state.profile_database_root = (
        Path(profile_database_root).resolve()
        if profile_database_root
        else PACKAGE_ROOT / "databases"
    )
    application.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")

    from cascade_forge.api.workbench import register_workbench

    register_workbench(application, templates)

    @application.get("/assets/plotly.min.js")
    def plotly_javascript() -> Response:
        return Response(get_plotlyjs(), media_type="application/javascript")

    @application.get("/health")
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "schema_version": "2.1.0",
                "v2": {"total": store_v2.count()},
            }
        )

    from cascade_forge.pages.routes import register_routes as register_pages

    register_pages(application)
    from cascade_forge.api.v2 import register_routes as register_v2

    register_v2(application)
    from cascade_forge.api.arc import register_routes as register_arc

    register_arc(application)
    from cascade_forge.api.workspace import register_routes as register_workspace

    register_workspace(application)
    from cascade_forge.api.jobs import register_routes as register_jobs

    register_jobs(application)

    return application
