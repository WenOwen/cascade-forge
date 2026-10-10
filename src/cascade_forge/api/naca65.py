from __future__ import annotations

import csv
import io
from typing import Any, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import HTTPException, Query
from fastapi.responses import JSONResponse, Response

from cascade_forge.api.coordinates import _naca65_coordinate_rows
from cascade_forge.api.schemas import (
    NACA65DatRequest,
    NACA65FitRequest,
    NACA65PresetRequest,
)
from cascade_forge.domain.export_igg import naca65_igg_dat, naca65_igg_split_zip
from cascade_forge.domain.fitting_naca65 import fit_reference_geometry
from cascade_forge.domain.models_naca65 import (
    NACA65BenchmarkConfig,
    NACA65Spec,
)
from cascade_forge.domain.naca65 import generate_naca65, parse_naca65_designation
from cascade_forge.workbench.benchmark_naca65 import run_naca65_benchmark


def register_routes(application):
    state = application.state
    naca65_store = state.naca65_store
    jobs_naca65 = state.jobs_naca65

    @application.post("/api/v2/naca65/generate")
    def generate_naca65_api(spec: NACA65Spec) -> dict[str, Any]:
        try:
            reference = generate_naca65(spec)
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"reference": reference.model_dump(mode="json")}

    @application.post("/api/v2/naca65/presets/parse")
    def parse_naca65_preset(body: NACA65PresetRequest) -> dict[str, Any]:
        try:
            spec = parse_naca65_designation(body.designation)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"spec": spec.model_dump(mode="json")}

    @application.post("/api/v2/naca65/fit")
    def fit_naca65_api(body: NACA65FitRequest) -> dict[str, Any]:
        try:
            reference = generate_naca65(body.spec)
            report, fitted = fit_reference_geometry(
                reference,
                max_outer_iterations=body.max_outer_iterations,
                max_design_evaluations=body.max_design_evaluations,
            )
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "reference": reference.model_dump(mode="json"),
            "fit_report": report.model_dump(mode="json"),
            "fitted_geometry": fitted.model_dump(mode="json"),
        }

    @application.post("/api/v2/naca65/export/dat")
    def export_current_naca65_dat(body: NACA65DatRequest) -> Response:
        try:
            case = body.model_dump(mode="json")
            content = (
                naca65_igg_split_zip(case, body.source)
                if body.layout == "split"
                else naca65_igg_dat(case, body.source)
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        extension = "zip" if body.layout == "split" else "dat"
        return Response(
            content,
            media_type="application/zip" if body.layout == "split" else "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="naca65-{body.source}.{extension}"'
            },
        )

    @application.post("/api/v2/naca65/benchmarks", status_code=202)
    def start_naca65_benchmark(
        body: NACA65BenchmarkConfig,
    ) -> dict[str, str]:
        def run() -> dict[str, Any]:
            return run_naca65_benchmark(body, naca65_store.root).model_dump(mode="json")

        job_id = jobs_naca65.submit("naca65_benchmark", body.model_dump(mode="json"), run)
        return {"job_id": job_id, "status": "queued"}

    @application.get("/api/v2/naca65/manifest")
    def get_naca65_manifest() -> dict[str, Any]:
        manifest = naca65_store.latest_manifest()
        if manifest is None:
            raise HTTPException(404, "NACA 65 基准尚未运行")
        return manifest.model_dump(mode="json")

    @application.get("/api/v2/naca65/cases")
    def list_naca65_cases(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        status: str | None = None,
        family: str | None = None,
        sort: str = "designation",
        descending: bool = False,
    ) -> dict[str, Any]:
        try:
            items = naca65_store.list_cases(
                limit=limit,
                offset=offset,
                status=status,
                family=family,
                sort=sort,
                descending=descending,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "items": items,
            "total": naca65_store.count(),
            "status_distribution": naca65_store.status_distribution(),
            "limit": limit,
            "offset": offset,
        }

    @application.get("/api/v2/naca65/cases/{fit_id}")
    def get_naca65_case(fit_id: str) -> dict[str, Any]:
        case = naca65_store.get_case(fit_id)
        if case is None:
            raise HTTPException(404, f"找不到 {fit_id}")
        return case

    @application.get("/api/v2/naca65/cases/{fit_id}/export")
    def export_naca65_case(
        fit_id: str,
        format: str = "csv",
        source: Literal["native", "fitted"] = "native",
        layout: Literal["closed", "split"] = "closed",
    ) -> Response:
        case = naca65_store.get_case(fit_id)
        if case is None:
            raise HTTPException(404, f"找不到 {fit_id}")
        if format == "dat":
            try:
                content = (
                    naca65_igg_split_zip(case, source)
                    if layout == "split"
                    else naca65_igg_dat(case, source)
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            extension = "zip" if layout == "split" else "dat"
            return Response(
                content,
                media_type="application/zip" if layout == "split" else "application/octet-stream",
                headers={
                    "Content-Disposition": f'attachment; filename="{fit_id}-{source}.{extension}"'
                },
            )
        if format == "json":
            return JSONResponse(
                case,
                headers={"Content-Disposition": f'attachment; filename="{fit_id}.json"'},
            )
        rows = _naca65_coordinate_rows(case)
        if format == "csv":
            stream = io.StringIO()
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
            return Response(
                stream.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{fit_id}.csv"'},
            )
        if format == "parquet":
            stream = pa.BufferOutputStream()
            pq.write_table(pa.Table.from_pylist(rows), stream, compression="zstd")
            return Response(
                stream.getvalue().to_pybytes(),
                media_type="application/vnd.apache.parquet",
                headers={"Content-Disposition": f'attachment; filename="{fit_id}.parquet"'},
            )
        raise HTTPException(422, "format 仅支持 dat、csv、json 或 parquet")
