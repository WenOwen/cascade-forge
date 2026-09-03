"""Cascade Forge public API."""

from .benchmark_naca65 import run_naca65_benchmark
from .blade3d import build_blade
from .features import extract_features
from .fitting_naca65 import evaluate_reference_fit, fit_reference_geometry
from .fitting_v2 import (
    fit_anchor_geometry,
    fit_anchors_to_dataset,
    fit_legacy_anchors,
)
from .geometry2d import generate_profile
from .geomturbo import export_geomturbo
from .legacy import ingest_legacy_anchors
from .models_naca65 import (
    NACA65BenchmarkConfig,
    NACA65BenchmarkManifest,
    NACA65Family,
    NACA65FitGeometry,
    NACA65FitReport,
    NACA65Geometry,
    NACA65InputMode,
    NACA65Spec,
    NACA65SplineSpec,
)
from .models_v2 import (
    AirfoilGeometryV2,
    AnchorFitRecord,
    BSplineCurveSpec,
    EdgeSpec,
    GeometryFeaturesV2,
    GeometryIntentV2,
    GeometrySpecV2,
    MetalAngleReference,
)
from .naca65 import generate_naca65, parse_naca65_designation
from .normal_bspline import generate_normal_offset_profile
from .sampling import generate_dataset
from .storage_naca65 import NACA65BenchmarkStore
from .storage_v2 import DatasetStoreV2
from .validation import validate_profile
from .workspace import WorkspaceStore

__all__ = [
    "build_blade",
    "run_naca65_benchmark",
    "export_geomturbo",
    "extract_features",
    "fit_anchor_geometry",
    "fit_anchors_to_dataset",
    "fit_legacy_anchors",
    "fit_reference_geometry",
    "evaluate_reference_fit",
    "generate_dataset",
    "generate_profile",
    "ingest_legacy_anchors",
    "validate_profile",
    "DatasetStoreV2",
    "NACA65BenchmarkStore",
    "WorkspaceStore",
    "AirfoilGeometryV2",
    "AnchorFitRecord",
    "BSplineCurveSpec",
    "EdgeSpec",
    "GeometryIntentV2",
    "GeometryFeaturesV2",
    "GeometrySpecV2",
    "MetalAngleReference",
    "NACA65BenchmarkConfig",
    "NACA65BenchmarkManifest",
    "NACA65Family",
    "NACA65FitReport",
    "NACA65FitGeometry",
    "NACA65SplineSpec",
    "generate_normal_offset_profile",
    "NACA65Geometry",
    "NACA65InputMode",
    "NACA65Spec",
    "generate_naca65",
    "parse_naca65_designation",
]

__version__ = "2.1.0"
