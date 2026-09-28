"""Cascade Forge public API."""

from .benchmark_naca65 import run_naca65_benchmark
from .blade3d import build_blade
from .circular_arc import (
    CircularArc,
    CircularArcChain,
    ThicknessLawInfeasible,
    aft_end_slope_for_wedge,
    arc_feasibility_report,
    build_arc_chain,
    build_thickness_law,
    default_double_arc_spec,
    double_arc_first_span,
    generate_arc_profile,
    interior_station_bounds,
    junction_station_bounds_deg,
    junction_station_range,
    maximum_feasible_half_wedge_deg,
    trailing_edge_half_wedges_deg,
)
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
from .models_circular_arc import (
    ARC_ALGORITHM_VERSION,
    ArcFamily,
    ArcMeanLineSpec,
    ArcProfileGeometry,
    ArcProfileSpec,
    MeanLineClosure,
    build_designation,
    build_mca_mean_line,
)
from .models_naca65 import (
    NACA65BenchmarkConfig,
    NACA65BenchmarkManifest,
    NACA65CamberCurveSpec,
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
    "aft_end_slope_for_wedge",
    "build_arc_chain",
    "build_designation",
    "build_mca_mean_line",
    "build_thickness_law",
    "default_double_arc_spec",
    "generate_arc_profile",
    "arc_feasibility_report",
    "double_arc_first_span",
    "interior_station_bounds",
    "junction_station_bounds_deg",
    "junction_station_range",
    "maximum_feasible_half_wedge_deg",
    "trailing_edge_half_wedges_deg",
    "ThicknessLawInfeasible",
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
    "ArcFamily",
    "ArcMeanLineSpec",
    "ArcProfileGeometry",
    "ArcProfileSpec",
    "ARC_ALGORITHM_VERSION",
    "BSplineCurveSpec",
    "CircularArc",
    "CircularArcChain",
    "EdgeSpec",
    "GeometryIntentV2",
    "GeometryFeaturesV2",
    "GeometrySpecV2",
    "MeanLineClosure",
    "MetalAngleReference",
    "NACA65BenchmarkConfig",
    "NACA65BenchmarkManifest",
    "NACA65CamberCurveSpec",
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
