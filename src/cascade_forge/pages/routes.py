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
    naca65_store = state.naca65_store

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

    @application.get("/naca65", response_class=HTMLResponse)
    def naca65_page(request: Request) -> HTMLResponse:
        context = _page_context("naca65", store_v2, workspace)
        context["naca65_count"] = naca65_store.count()
        context["naca65_manifest"] = naca65_store.latest_manifest()
        return templates.TemplateResponse(request, "pages/naca65.html", context)

    @application.get("/blade", response_class=HTMLResponse)
    def blade_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "pages/blade.html",
            _page_context("blade", store_v2, workspace),
        )

    @application.get("/arc", response_class=HTMLResponse)
    def arc_page(request: Request) -> HTMLResponse:
        context = _page_context("arc", store_v2, workspace)
        # 模板用服务端渲染的默认值（与 ArcProfileSpec 的默认合同保持一致），
        # 页面首屏先调用 /api/v2/arc/limits 把可行域填进控件再生成。
        context["default_arc_spec"] = default_double_arc_spec().model_dump(mode="json")
        return templates.TemplateResponse(request, "pages/arc.html", context)
