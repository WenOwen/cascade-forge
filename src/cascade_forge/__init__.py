"""Cascade Forge public API."""

from cascade_forge.domain.blade3d import build_blade
from cascade_forge.domain.circular_arc import (
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
from cascade_forge.domain.features import extract_features
from cascade_forge.domain.fitting_naca65 import evaluate_reference_fit, fit_reference_geometry
from cascade_forge.domain.geometry import generate_profile
from cascade_forge.domain.geomturbo import export_geomturbo
from cascade_forge.domain.models import (
    AirfoilGeometryV2,
    BSplineCurveSpec,
    EdgeSpec,
    GeometryFeaturesV2,
    GeometryIntentV2,
    GeometrySpecV2,
    MetalAngleReference,
)
from cascade_forge.domain.models_circular_arc import (
    ARC_ALGORITHM_VERSION,
    ArcFamily,
    ArcMeanLineSpec,
    ArcProfileGeometry,
    ArcProfileSpec,
    MeanLineClosure,
    build_designation,
    build_mca_mean_line,
)
from cascade_forge.domain.models_naca65 import (
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
from cascade_forge.domain.naca65 import generate_naca65, parse_naca65_designation
from cascade_forge.domain.normal_bspline import generate_normal_offset_profile
from cascade_forge.domain.validation import validate_profile
from cascade_forge.storage.dataset import DatasetStoreV2
from cascade_forge.storage.naca65 import NACA65BenchmarkStore
from cascade_forge.storage.workspace import WorkspaceStore
from cascade_forge.workbench.benchmark_naca65 import run_naca65_benchmark

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
    "fit_reference_geometry",
    "evaluate_reference_fit",
    "generate_profile",
    "validate_profile",
    "DatasetStoreV2",
    "NACA65BenchmarkStore",
    "WorkspaceStore",
    "AirfoilGeometryV2",
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
