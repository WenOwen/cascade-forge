from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from cascade_forge.domain.models import (
    GeometrySpecV2,
)
from cascade_forge.domain.models_naca65 import (
    NACA65FitGeometry,
    NACA65Geometry,
    NACA65Spec,
)


class WorkspaceDesignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list)
    spec: GeometrySpecV2


class CompareRequestV2(BaseModel):
    geometry_ids: list[str] = Field(min_length=2, max_length=12)


class CascadeRequestV2(BaseModel):
    geometry_id: str
    solidity: float = Field(ge=0.7, le=2.2)


class NACA65FitRequest(BaseModel):
    spec: NACA65Spec
    max_outer_iterations: int = Field(default=6, ge=1, le=20)
    max_design_evaluations: int = Field(default=80, ge=10, le=500)


class NACA65PresetRequest(BaseModel):
    designation: str = Field(min_length=5, max_length=40)


class NACA65DatRequest(BaseModel):
    reference: NACA65Geometry
    fitted_geometry: NACA65FitGeometry | None = None
    source: Literal["native", "fitted"] = "native"
    layout: Literal["closed", "split"] = "closed"
