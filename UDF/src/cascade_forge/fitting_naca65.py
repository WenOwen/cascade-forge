from __future__ import annotations

import hashlib
import json
import math
import time

import numpy as np

from .features import extract_features
from .fitting_v2 import _symmetric_distance_metrics, fit_anchor_geometry
from .models import AirfoilGeometry
from .models_naca65 import NACA65FitReport, NACA65Geometry
from .models_v2 import AirfoilGeometryV2


def fit_reference_geometry(
    reference: NACA65Geometry,
    *,
    max_outer_iterations: int = 6,
    max_design_evaluations: int = 80,
) -> tuple[NACA65FitReport, AirfoilGeometryV2]:
    """Fit a normalized reference profile with the current B-spline × B-spline kernel."""

    started = time.perf_counter()
    surrogate = _reference_airfoil(reference)
    anchor_record, fitted = fit_anchor_geometry(
        surrogate,
        max_outer_iterations=max_outer_iterations,
        max_design_evaluations=max_design_evaluations,
    )
    fitted = fitted.model_copy(
        update={"source_id": reference.native_id, "metadata": {
            **fitted.metadata,
            "reference_kind": "naca65_native",
            "native_id": reference.native_id,
        }}
    )
    report = evaluate_reference_fit(
        reference,
        fitted,
        iterations=anchor_record.iterations,
        converged=anchor_record.converged,
        termination_reason=anchor_record.termination_reason,
        elapsed_seconds=time.perf_counter() - started,
        warnings=anchor_record.source_warnings,
        fit_config={
            "max_outer_iterations": max_outer_iterations,
            "max_design_evaluations": max_design_evaluations,
        },
    )
    return report, fitted


def evaluate_reference_fit(
    reference: NACA65Geometry,
    fitted: AirfoilGeometryV2,
    *,
    iterations: int = 0,
    converged: bool = False,
    termination_reason: str = "evaluation_only",
    elapsed_seconds: float = 0.0,
    warnings: list[str] | None = None,
    fit_config: dict[str, int] | None = None,
) -> NACA65FitReport:
    source_suction = np.asarray(reference.normalized_suction, dtype=float)
    source_pressure = np.asarray(reference.normalized_pressure, dtype=float)
    source_camber = np.asarray(reference.camber, dtype=float)
    fitted_suction, fitted_pressure, _, _ = fitted.arrays()
    source_outline = np.vstack([source_suction, source_pressure])
    fitted_outline = np.vstack([fitted_suction, fitted_pressure])
    rms, maximum, hausdorff = _symmetric_distance_metrics(source_outline, fitted_outline)
    core_domain = (0.02, 0.98)
    source_core = _core_points((source_suction, source_pressure), core_domain)
    fitted_core = _core_points((fitted_suction, fitted_pressure), core_domain)
    core_rms, core_maximum, _ = _symmetric_distance_metrics(source_core, fitted_core)

    normals = _unit_normals(source_camber)
    fitted_suction_matched = _resample_by_arclength(fitted_suction, len(source_suction))
    fitted_pressure_matched = _resample_by_arclength(fitted_pressure, len(source_pressure))
    suction_error = np.sum((fitted_suction_matched - source_suction) * normals, axis=1)
    pressure_error = np.sum((fitted_pressure_matched - source_pressure) * normals, axis=1)
    feature_deltas = _feature_deltas(reference, fitted)
    area_difference = _outline_area(fitted_suction, fitted_pressure) - _outline_area(
        source_suction, source_pressure
    )
    status = (
        "valid"
        if rms <= 5e-4 and maximum <= 2e-3
        else "warning"
        if rms <= 1.5e-3 and maximum <= 5e-3
        else "rejected"
    )
    fit_payload = {
        "native_id": reference.native_id,
        "geometry_id": fitted.geometry_id,
        "generator_version": fitted.spec.generator_version,
        "fit_config": fit_config or {},
    }
    fit_id = "naca65_fit_" + hashlib.sha256(
        json.dumps(fit_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:24]
    return NACA65FitReport(
        fit_id=fit_id,
        native_id=reference.native_id,
        geometry_id=fitted.geometry_id,
        fit_status=status,
        rms_distance_ratio=rms,
        max_distance_ratio=maximum,
        hausdorff_approx_ratio=hausdorff,
        core_rms_distance_ratio=core_rms,
        core_max_distance_ratio=core_maximum,
        suction_signed_normal_error=suction_error.tolist(),
        pressure_signed_normal_error=pressure_error.tolist(),
        area_difference_ratio=float(area_difference),
        feature_deltas=feature_deltas,
        iterations=iterations,
        converged=converged,
        termination_reason=termination_reason,
        elapsed_seconds=elapsed_seconds,
        explicit_spec=fitted.spec.model_dump(mode="json"),
        warnings=warnings or [],
    )


def _reference_airfoil(reference: NACA65Geometry) -> AirfoilGeometry:
    suction = np.asarray(reference.normalized_suction, dtype=float)
    pressure = np.asarray(reference.normalized_pressure, dtype=float)
    leading_radius = _edge_radius(suction[1], suction[0], pressure[1])
    trailing_radius = _edge_radius(suction[-2], suction[-1], pressure[-2])
    return AirfoilGeometry(
        geometry_id=reference.native_id,
        origin="anchor",
        source_id=reference.native_id,
        suction=reference.normalized_suction,
        pressure=reference.normalized_pressure,
        camber=reference.camber,
        thickness=reference.thickness,
        realized_stagger_deg=0.0,
        metadata={
            "leading_edge_radius_ratio": leading_radius,
            "trailing_edge_radius_ratio": trailing_radius,
            "source_warnings": [
                "NACA 原始尾缘已按 OpenVSP 兼容规则闭合后用于拟合；原坐标保持不变"
            ],
        },
    )


def _feature_deltas(
    reference: NACA65Geometry,
    fitted: AirfoilGeometryV2,
) -> dict[str, float]:
    suction = np.asarray(reference.normalized_suction)
    pressure = np.asarray(reference.normalized_pressure)
    camber = np.asarray(reference.camber)
    thickness = np.asarray(reference.thickness)
    max_thickness_index = int(np.argmax(thickness))
    camber_index = int(np.argmax(np.abs(camber[:, 1])))
    source_values = {
        "max_thickness_ratio": float(thickness[max_thickness_index]),
        "max_thickness_x_ratio": float(camber[max_thickness_index, 0]),
        "max_camber_ratio": float(abs(camber[camber_index, 1])),
        "max_camber_x_ratio": float(camber[camber_index, 0]),
        "leading_edge_radius_ratio": _edge_radius(suction[1], suction[0], pressure[1]),
        "trailing_edge_radius_ratio": _edge_radius(
            suction[-2], suction[-1], pressure[-2]
        ),
        "inlet_local_angle_deg": _endpoint_angle(camber, start=True),
        "outlet_local_angle_deg": _endpoint_angle(camber, start=False),
    }
    fitted_features = extract_features(fitted)
    return {
        name: float(getattr(fitted_features, name) - value)
        for name, value in source_values.items()
    }


def _core_points(
    surfaces: tuple[np.ndarray, np.ndarray],
    domain: tuple[float, float],
) -> np.ndarray:
    selected = [
        surface[(surface[:, 0] >= domain[0]) & (surface[:, 0] <= domain[1])]
        for surface in surfaces
    ]
    return np.vstack(selected)


def _unit_normals(points: np.ndarray) -> np.ndarray:
    delta = np.gradient(points, axis=0, edge_order=2)
    length = np.maximum(np.linalg.norm(delta, axis=1), 1e-15)
    return np.column_stack([-delta[:, 1], delta[:, 0]]) / length[:, None]


def _resample_by_arclength(points: np.ndarray, count: int) -> np.ndarray:
    segment = np.linalg.norm(np.diff(points, axis=0), axis=1)
    parameter = np.concatenate([[0.0], np.cumsum(segment)])
    parameter /= parameter[-1]
    target = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, count)))
    return np.column_stack(
        [np.interp(target, parameter, points[:, axis]) for axis in range(2)]
    )


def _edge_radius(first: np.ndarray, vertex: np.ndarray, last: np.ndarray) -> float:
    a = float(np.linalg.norm(vertex - last))
    b = float(np.linalg.norm(first - last))
    c = float(np.linalg.norm(first - vertex))
    first_vector = vertex - first
    second_vector = last - first
    cross = abs(
        float(first_vector[0] * second_vector[1] - first_vector[1] * second_vector[0])
    )
    if cross <= 1e-15:
        return 1e-4
    return float(np.clip(a * b * c / (2.0 * cross), 1e-4, 0.03))


def _endpoint_angle(camber: np.ndarray, *, start: bool) -> float:
    points = camber[:7] if start else camber[-7:]
    slope = float(np.polyfit(points[:, 0], points[:, 1], 1)[0])
    return math.degrees(math.atan(slope))


def _outline_area(suction: np.ndarray, pressure: np.ndarray) -> float:
    outline = np.vstack([suction, pressure[-2:0:-1]])
    x = outline[:, 0]
    y = outline[:, 1]
    return 0.5 * abs(float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1))))
