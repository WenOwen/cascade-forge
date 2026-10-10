from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from cascade_forge.domain.circular_arc import (
    arc_feasibility_report,
    generate_arc_profile,
)
from cascade_forge.domain.models_circular_arc import ArcProfileSpec


def register_routes(application):

    @application.post("/api/v2/arc/generate")
    def generate_arc_api(spec: ArcProfileSpec) -> dict[str, Any]:
        try:
            geometry = generate_arc_profile(spec)
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"geometry": geometry.model_dump(mode="json")}

    @application.post("/api/v2/arc/limits")
    def arc_limits_api(spec: ArcProfileSpec) -> dict[str, Any]:
        # 可行域报告必须用**同一条** spec 求值：楔角上界依赖中弧线总弧长 L、出口金属角
        # χ₂ 与尾缘曲率 κ，前缘半径标称量还会影响前段单调性判据。省略它们会报出一个
        # 生成器实现不了的数（旧实现偏高约 3%，且上界之下 0.01° 就被拒绝）。
        try:
            return arc_feasibility_report(spec)
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(422, str(exc)) from exc
