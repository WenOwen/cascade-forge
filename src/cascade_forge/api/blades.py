from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from cascade_forge.domain.blade3d import build_blade
from cascade_forge.domain.geomturbo import export_geomturbo
from cascade_forge.domain.models import (
    SectionSchedule,
)


def register_routes(application):
    state = application.state
    store_v2 = state.store_v2
    jobs_v2 = state.jobs_v2
    resolved_v2_root = state.v2_dataset_root

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
