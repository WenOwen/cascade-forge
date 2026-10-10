from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def register_routes(application):
    state = application.state
    store_v2 = state.store_v2

    @application.get("/api/v2/jobs/{job_id}")
    def get_job_v2(job_id: str) -> dict[str, Any]:
        job = store_v2.get_job(job_id)
        if job is None:
            raise HTTPException(404, f"找不到 {job_id}")
        return job
