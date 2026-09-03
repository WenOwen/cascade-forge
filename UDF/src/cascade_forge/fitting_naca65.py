from __future__ import annotations

import hashlib
import json
import math
import time

import numpy as np

from .curves import CurveEvaluator
from .features import _polyline_self_intersects
from .models_naca65 import NACA65FitGeometry, NACA65FitReport, NACA65Geometry
from .naca65 import _normalize_and_resample
from .normal_bspline import (
    evaluate_normal_offset,
    fit_decomposition,
    generate_normal_offset_profile,
)


def fit_reference_geometry(
    reference: NACA65Geometry,
    *,
    max_outer_iterations: int = 6,
    max_design_evaluations: int = 80,
) -> tuple[NACA65FitReport, NACA65FitGeometry]:
    """Fit the actual mean line, then one shared regularized thickness spline.

    max_outer_iterations remains for HTTP/CLI compatibility, not surface optimization.
    """
    started = time.perf_counter()
    spec, result = fit_decomposition(reference, max_design_evaluations)
    fitted = generate_normal_offset_profile(spec, reference.native_id)
    warnings = [
        "尖尾缘半径为 0：没有伪造正半径或独立再拟合叶面。",
        "NASA 在 x/c<0.005 使用受限构造斜率；端区法向与严格中弧线导数存在原生差异。",
    ]
    if reference.spec.family.value == "65A":
        warnings.append("65A 原生直线尾段仍作为参考真值；共享法向模型的尾段偏差计入误差。")
    report = evaluate_reference_fit(
        reference,
        fitted,
        iterations=max(1, result.nit),
        converged=bool(result.success),
        termination_reason="independent_camber_and_shared_thickness_lsq",
        warnings=warnings,
        fit_config={
            "max_outer_iterations": max_outer_iterations,
            "max_design_evaluations": max_design_evaluations,
        },
    )
    report.elapsed_seconds = time.perf_counter() - started
    if not result.success:
        report.fit_status = "rejected"
        report.warnings.append("厚度拟合未收敛；即使轮廓误差较小也不能作为有效记录。")
    return report, fitted


def point_to_polyline(points: np.ndarray, polyline: np.ndarray) -> np.ndarray:
    """Exact point-to-segment distances, not nearest sampled-vertex distances."""
    a, vector = polyline[:-1], np.diff(polyline, axis=0)
    length2 = np.sum(vector * vector, axis=1)
    distances = []
    for chunk in np.array_split(points, max(1, math.ceil(len(points) / 64))):
        delta = chunk[:, None, :] - a[None, :, :]
        fraction = np.clip(
            np.sum(delta * vector[None, :, :], axis=2) / np.maximum(length2, 1e-30), 0, 1
        )
        error = delta - fraction[:, :, None] * vector[None, :, :]
        distances.append(np.sqrt(np.min(np.sum(error * error, axis=2), axis=1)))
    return np.concatenate(distances)


def evaluate_reference_fit(
    reference: NACA65Geometry,
    fitted: NACA65FitGeometry,
    *,
    iterations: int = 0,
    converged: bool = False,
    termination_reason: str = "evaluation_only",
    elapsed_seconds: float = 0.0,
    warnings: list[str] | None = None,
    fit_config: dict[str, int] | None = None,
) -> NACA65FitReport:
    source = (np.asarray(reference.normalized_suction), np.asarray(reference.normalized_pressure))
    output = (np.asarray(fitted.suction), np.asarray(fitted.pressure))
    dense_source = _normalize_and_resample(
        np.asarray(reference.raw_suction), np.asarray(reference.raw_pressure), 4001
    )
    dense_u = 0.5 * (1 - np.cos(np.linspace(0, np.pi, 4001)))
    dense_fit = evaluate_normal_offset(fitted.spec, dense_u)[:2]
    distances, core_distances, signed_errors = [], [], []
    sample = 0.5 * (1 - np.cos(np.linspace(0, np.pi, len(source[0]))))
    for side, (src, dst, dense_src, dense_dst) in enumerate(
        zip(source, output, dense_source, dense_fit, strict=True)
    ):
        ds, df = point_to_polyline(src, dense_dst), point_to_polyline(dst, dense_src)
        distances.extend([ds, df])
        core_distances.extend(
            [
                ds[(src[:, 0] >= 0.02) & (src[:, 0] <= 0.98)],
                df[(dst[:, 0] >= 0.02) & (dst[:, 0] <= 0.98)],
            ]
        )
        matched = _resample_by_arclength(dense_dst, sample)
        delta = np.gradient(src, axis=0, edge_order=2)
        normal = np.column_stack([-delta[:, 1], delta[:, 0]])
        normal /= np.maximum(np.linalg.norm(normal, axis=1)[:, None], 1e-15)
        if side == 1:
            normal *= -1
        signed_errors.append(np.sum((matched - src) * normal, axis=1).tolist())
    distances = np.concatenate(distances)
    core_distances = np.concatenate(core_distances)
    rms, maximum = float(np.sqrt(np.mean(distances**2))), float(distances.max())
    status = (
        "valid"
        if rms <= 5e-4 and maximum <= 2e-3
        else "warning"
        if rms <= 1.5e-3 and maximum <= 5e-3
        else "rejected"
    )
    camber_delta = np.asarray(fitted.camber) - np.asarray(reference.camber)
    diagnostics = {
        **fitted.edge_metrics,
        "camber_rms_distance_ratio": float(np.sqrt(np.mean(np.sum(camber_delta**2, axis=1)))),
        "camber_max_distance_ratio": float(np.linalg.norm(camber_delta, axis=1).max()),
        "half_thickness_rms_ratio": float(
            np.sqrt(
                np.mean(((np.asarray(fitted.thickness) - np.asarray(reference.thickness)) / 2) ** 2)
            )
        ),
        "distance_polyline_points_per_side": 4001.0,
    }
    # Uniform decimation of the dense cosine grid preserves nose resolution.
    outline = np.vstack([dense_fit[0][::5], dense_fit[1][::5][-2:0:-1]])
    intersects = _polyline_self_intersects(outline, closed=True)
    diagnostics["outline_self_intersects"] = float(intersects)
    if intersects:
        status = "rejected"
    fit_payload = {
        "native_id": reference.native_id,
        "geometry_id": fitted.geometry_id,
        "generator_version": fitted.spec.generator_version,
        "fit_config": fit_config or {},
        "evaluation_version": "point-to-polyline-v2",
    }
    fit_id = (
        "naca65_fit_"
        + hashlib.sha256(
            json.dumps(fit_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:24]
    )
    return NACA65FitReport(
        fit_id=fit_id,
        native_id=reference.native_id,
        geometry_id=fitted.geometry_id,
        fit_status=status,
        rms_distance_ratio=rms,
        max_distance_ratio=maximum,
        hausdorff_approx_ratio=maximum,
        core_rms_distance_ratio=float(np.sqrt(np.mean(core_distances**2))),
        core_max_distance_ratio=float(core_distances.max()),
        suction_signed_normal_error=signed_errors[0],
        pressure_signed_normal_error=signed_errors[1],
        area_difference_ratio=_outline_area(*output) - _outline_area(*source),
        feature_deltas=_feature_deltas(reference, fitted),
        iterations=iterations,
        converged=converged,
        termination_reason=termination_reason,
        elapsed_seconds=elapsed_seconds,
        explicit_spec=fitted.spec.model_dump(mode="json"),
        warnings=warnings or [],
        diagnostics=diagnostics,
        error_parameter=sample.tolist(),
    )


def _feature_deltas(reference: NACA65Geometry, fitted: NACA65FitGeometry) -> dict[str, float]:
    def shape_features(camber, thickness):
        c, t = np.asarray(camber), np.asarray(thickness)
        peak_t, peak_c = int(np.argmax(t)), int(np.argmax(np.abs(c[:, 1])))
        return {
            "max_thickness_ratio": float(t[peak_t]),
            "max_thickness_x_ratio": float(c[peak_t, 0]),
            "max_camber_ratio": float(abs(c[peak_c, 1])),
            "max_camber_x_ratio": float(c[peak_c, 0]),
        }

    src = shape_features(reference.camber, reference.thickness)
    dst = shape_features(fitted.camber, fitted.thickness)
    source_tangent = np.asarray(reference.construction_normals)[[0, -1]][:, [1, 0]]
    source_tangent[:, 1] *= -1
    fitted_tangent = CurveEvaluator(fitted.spec.camber_curve).evaluate([0.0, 1.0], 1)
    for i, name in enumerate(("inlet_local_angle_deg", "outlet_local_angle_deg")):
        src[name] = math.degrees(math.atan2(*source_tangent[i, ::-1]))
        dst[name] = math.degrees(math.atan2(*fitted_tangent[i, ::-1]))
    ss, ps = np.asarray(reference.raw_suction), np.asarray(reference.raw_pressure)
    src["leading_edge_radius_ratio"] = _edge_radius(ss[1], ss[0], ps[1])
    src["trailing_edge_radius_ratio"] = 0.0
    dst.update(
        {
            name: fitted.edge_metrics[name]
            for name in ("leading_edge_radius_ratio", "trailing_edge_radius_ratio")
        }
    )
    return {name: dst[name] - value for name, value in src.items()}


def _resample_by_arclength(points: np.ndarray, target: np.ndarray) -> np.ndarray:
    parameter = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    parameter /= parameter[-1]
    return np.column_stack([np.interp(target, parameter, points[:, a]) for a in range(2)])


def _edge_radius(first: np.ndarray, vertex: np.ndarray, last: np.ndarray) -> float:
    a, b, c = (
        np.linalg.norm(vertex - last),
        np.linalg.norm(first - last),
        np.linalg.norm(first - vertex),
    )
    v, w = vertex - first, last - first
    cross = abs(float(v[0] * w[1] - v[1] * w[0]))
    return float(a * b * c / (2 * cross)) if cross > 1e-18 else 0.0


def _outline_area(suction: np.ndarray, pressure: np.ndarray) -> float:
    outline = np.vstack([suction, pressure[-2:0:-1]])
    x, y = outline[:, 0], outline[:, 1]
    return 0.5 * abs(float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1))))
