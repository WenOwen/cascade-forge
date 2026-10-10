from __future__ import annotations

from typing import Any

from cascade_forge.domain.models import (
    GeometrySpecV2,
)
from cascade_forge.storage.dataset import DatasetStoreV2
from cascade_forge.storage.workspace import WorkspaceStore


def _default_spec_v2() -> GeometrySpecV2:
    from cascade_forge.domain.models import GeometryIntentV2

    return GeometrySpecV2(
        intent=GeometryIntentV2(
            inlet_metal_angle_deg=55.0,
            outlet_metal_angle_deg=30.0,
            stagger_deg=42.0,
            max_thickness_ratio=0.10,
            max_thickness_x_ratio=0.42,
            leading_edge_radius_ratio=0.008,
            trailing_edge_radius_ratio=0.002,
        )
    )


def _page_context(page: str, store: DatasetStoreV2, workspace: WorkspaceStore) -> dict[str, Any]:
    return {
        "page": page,
        "v2_generated": store.count("generated"),
        "v2_rejected": store.rejected_count(),
        "workspace_count": len(workspace.list_designs(limit=1000)),
    }
