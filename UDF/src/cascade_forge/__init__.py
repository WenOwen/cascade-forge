"""Cascade Forge public API."""

from .blade3d import build_blade
from .features import extract_features
from .geometry2d import generate_profile
from .geomturbo import export_geomturbo
from .legacy import ingest_legacy_anchors
from .models_v2 import (
    AirfoilGeometryV2,
    BezierCurveSpec,
    BSplineCurveSpec,
    CurveFamily,
    EdgeSpec,
    GeometryIntentV2,
    GeometrySpecV2,
    NURBSCurveSpec,
)
from .sampling import generate_dataset
from .validation import validate_profile

__all__ = [
    "build_blade",
    "export_geomturbo",
    "extract_features",
    "generate_dataset",
    "generate_profile",
    "ingest_legacy_anchors",
    "validate_profile",
    "AirfoilGeometryV2",
    "BSplineCurveSpec",
    "BezierCurveSpec",
    "CurveFamily",
    "EdgeSpec",
    "GeometryIntentV2",
    "GeometrySpecV2",
    "NURBSCurveSpec",
]

__version__ = "2.0.0"
