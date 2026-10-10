from __future__ import annotations

from pydantic import BaseModel, Field

from cascade_forge.domain.models import (
    GeometrySpecV2,
)


class WorkspaceDesignRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tags: list[str] = Field(default_factory=list)
    spec: GeometrySpecV2
