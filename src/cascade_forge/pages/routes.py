from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from cascade_forge.domain.circular_arc import (
    default_double_arc_spec,
)
from cascade_forge.pages.context import _default_spec_v2, _page_context


def register_routes(application):
    state = application.state
    templates = state.templates
    store_v2 = state.store_v2
    workspace = state.workspace

    @application.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        return studio_page(request)

    @application.get("/standard", response_class=HTMLResponse)
    def standard_page(request: Request) -> HTMLResponse:
        context = _page_context("standard", store_v2, workspace)
        context["default_arc_spec"] = default_double_arc_spec().model_dump(mode="json")
        return templates.TemplateResponse(request, "pages/standard.html", context)

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
