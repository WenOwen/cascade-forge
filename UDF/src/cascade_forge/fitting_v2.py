from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from pathlib import Path

import numpy as np
from scipy.interpolate import BSpline
from scipy.optimize import least_squares, lsq_linear

from .curves import CurveEvaluator, open_uniform_knots, unit_tangent_and_normal
from .features import extract_features
from .geometry_v2 import _fit_surface, generate_profile_v2
from .legacy import ROWS, _anchor_geometry
from .models import AirfoilGeometry, file_sha256
from .models_v2 import (
    AirfoilGeometryV2,
    AnchorFitRecord,
    BSplineCurveSpec,
    DatasetManifestV2,
    EdgeSpec,
    GeometryIntentV2,
    GeometrySpecV2,
)


def load_legacy_anchor_geometries(raw_root: str | Path) -> list[AirfoilGeometry]:
    """只读加载精选 NASA 48 截面，不写数据库且不改变源文件。"""

    root = Path(raw_root).resolve()
    anchors: list[AirfoilGeometry] = []
    for row_name in ROWS:
        row_root = root / row_name
        metadata = json.loads((row_root / "section_metadata.json").read_text(encoding="utf-8"))
        for section in metadata["sections"]:
            source_path = row_root / metadata["filename_pattern"].format(suffix=section["suffix"])
            anchors.append(
                _anchor_geometry(row_name, section, source_path, file_sha256(source_path))
            )
    if len(anchors) != 48:
        raise ValueError(f"NASA 锚点数量应为 48，实际为 {len(anchors)}")
    return anchors


def fit_anchor_geometry(
    anchor: AirfoilGeometry,
    *,
    max_outer_iterations: int = 6,
    max_design_evaluations: int = 80,
) -> tuple[AnchorFitRecord, AirfoilGeometryV2]:
    """用参数投影/设计更新交替法反参数化一个不可变 NASA 锚点。"""

    source_suction, source_pressure, source_camber, source_thickness = anchor.arrays()
    source_features = extract_features(anchor)
    intent, source_warnings = _anchor_intent(anchor, source_features)
    initial = _initial_design(
        source_suction,
        source_pressure,
        source_camber,
        source_features,
        intent,
    )
    lower, upper = _design_bounds(initial)
    sample_index = np.unique(np.linspace(0, len(source_suction) - 1, 121).astype(int))
    target_suction = source_suction[sample_index]
    target_pressure = source_pressure[sample_index]
    beta = np.linspace(0.0, math.pi, len(source_suction))
    initial_u = 0.5 * (1.0 - np.cos(beta))[sample_index]
    suction_u = initial_u.copy()
    pressure_u = initial_u.copy()
    design = initial.copy()
    previous = math.inf
    converged = False
    termination = "maximum_outer_iterations"
    completed = 0

    for outer_index in range(1, max_outer_iterations + 1):
        completed = outer_index
        template = _spec_from_design(design, intent)
        suction_curve = CurveEvaluator(_fit_surface(template, suction=True).curve)
        pressure_curve = CurveEvaluator(_fit_surface(template, suction=False).curve)
        suction_u = _project_parameters(suction_curve, target_suction, suction_u)
        pressure_u = _project_parameters(pressure_curve, target_pressure, pressure_u)

        def residual(
            values: np.ndarray,
            suction_parameters: np.ndarray = suction_u,
            pressure_parameters: np.ndarray = pressure_u,
        ) -> np.ndarray:
            candidate = _spec_from_design(values, intent)
            suction_fit = CurveEvaluator(_fit_surface(candidate, suction=True).curve)
            pressure_fit = CurveEvaluator(_fit_surface(candidate, suction=False).curve)
            return np.concatenate(
                [
                    (suction_fit.evaluate(suction_parameters) - target_suction).ravel(),
                    (pressure_fit.evaluate(pressure_parameters) - target_pressure).ravel(),
                ]
            )

        result = least_squares(
            residual,
            design,
            bounds=(lower, upper),
            xtol=1e-10,
            ftol=1e-10,
            gtol=1e-10,
            max_nfev=max_design_evaluations,
            x_scale="jac",
        )
        design = result.x
        objective = float(np.linalg.norm(result.fun))
        relative = abs(previous - objective) / max(previous, 1e-15)
        if np.isfinite(previous) and relative <= 1e-8:
            converged = True
            termination = "relative_objective_change"
            break
        if result.optimality <= 1e-8:
            converged = True
            termination = "design_optimality"
            break
        previous = objective

    explicit = _spec_from_design(design, _intent_from_design(design, intent))
    fitted = generate_profile_v2(explicit).model_copy(
        update={"origin": "anchor_fit", "source_id": anchor.geometry_id}
    )
    fitted_suction, fitted_pressure, _, _ = fitted.arrays()
    rms, maximum, hausdorff = _symmetric_distance_metrics(
        np.vstack([source_suction, source_pressure]),
        np.vstack([fitted_suction, fitted_pressure]),
    )
    core_domain = (0.02, 0.98)
    source_core = np.vstack(
        [
            surface[(surface[:, 0] >= core_domain[0]) & (surface[:, 0] <= core_domain[1])]
            for surface in (source_suction, source_pressure)
        ]
    )
    fitted_core = np.vstack(
        [
            surface[(surface[:, 0] >= core_domain[0]) & (surface[:, 0] <= core_domain[1])]
            for surface in (fitted_suction, fitted_pressure)
        ]
    )
    core_rms, core_maximum, _ = _symmetric_distance_metrics(source_core, fitted_core)
    # NASA 原始截面保存有限厚前/尾缘，而统一 G2 内核使用共享鼻点；因此全轮廓
    # 误差作为拓扑差异诊断，拟合门槛使用不含鼻点邻域的主体叶面误差。
    status = (
        "valid"
        if core_rms <= 2e-3 and core_maximum <= 5e-3
        else "warning"
        if core_rms <= 2.5e-3 and core_maximum <= 8e-3
        else "rejected"
    )
    fit_key = f"{anchor.geometry_id}:{fitted.geometry_id}"
    record = AnchorFitRecord(
        fit_id="fit_v2_" + hashlib.sha256(fit_key.encode()).hexdigest()[:24],
        source_anchor_id=anchor.geometry_id,
        geometry_id=fitted.geometry_id,
        rms_distance_ratio=rms,
        max_distance_ratio=maximum,
        hausdorff_approx_ratio=hausdorff,
        core_rms_distance_ratio=core_rms,
        core_max_distance_ratio=core_maximum,
        core_domain_x_ratio=core_domain,
        fit_status=status,
        iterations=completed,
        converged=converged,
        termination_reason=termination,
        source_warnings=source_warnings,
        explicit_spec=explicit.model_dump(mode="json"),
    )
    return record, fitted


def fit_legacy_anchors(
    raw_root: str | Path,
    *,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> tuple[list[AnchorFitRecord], list[AirfoilGeometryV2]]:
    """每个原始锚点只生成一个统一内核派生拟合，共 48 条。"""

    records: list[AnchorFitRecord] = []
    geometries: list[AirfoilGeometryV2] = []
    anchors = load_legacy_anchor_geometries(raw_root)
    for index, anchor in enumerate(anchors, start=1):
        record, geometry = fit_anchor_geometry(anchor)
        records.append(record)
        geometries.append(geometry)
        if progress:
            progress({"completed": index, "total": 48})
    return records, geometries


def fit_anchors_to_dataset(
    raw_root: str | Path,
    dataset_root: str | Path,
    *,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> dict[str, int | bool]:
    """写入 48 个只读锚点和 48 个统一内核派生拟合。"""

    from .storage_v2 import DatasetStoreV2
    from .validation import validate_profile

    store = DatasetStoreV2(dataset_root)
    anchors = load_legacy_anchor_geometries(raw_root)
    if store.anchor_count() == 0:
        store.append_anchors(anchors)
    if store.fit_count() == 48 and store.count("anchor_fit") == 48:
        gate = store.anchor_gate_passed()
        _refresh_manifest_if_present(store, gate)
        return {
            "anchors": store.anchor_count(),
            "anchor_fits": store.fit_count(),
            "derived_geometries": store.count("anchor_fit"),
            "anchor_gate_passed": gate,
        }

    existing_fit_ids = store.fit_ids()
    existing_geometry_ids = store.geometry_ids()
    fit_records: list[AnchorFitRecord] = []
    geometry_rows = []
    for completed, anchor in enumerate(anchors, start=1):
        record, geometry = fit_anchor_geometry(anchor)
        report = validate_profile(geometry)
        if record.fit_id not in existing_fit_ids:
            fit_records.append(record)
            existing_fit_ids.add(record.fit_id)
        if geometry.geometry_id not in existing_geometry_ids:
            geometry_rows.append(
                (
                    geometry,
                    extract_features(geometry),
                    report,
                    {"proposal_index": None},
                )
            )
            existing_geometry_ids.add(geometry.geometry_id)
        if len(fit_records) >= 12:
            store.append_anchor_fits(fit_records)
            store.append_geometries(
                geometry_rows,
                batch_metadata={"kind": "anchor_fit", "completed": completed},
            )
            fit_records.clear()
            geometry_rows.clear()
        if progress:
            progress({"completed": completed, "total": 48})
    store.append_anchor_fits(fit_records)
    store.append_geometries(
        geometry_rows,
        batch_metadata={"kind": "anchor_fit", "completed": 48},
    )
    gate = store.anchor_gate_passed()
    _refresh_manifest_if_present(store, gate)
    return {
        "anchors": store.anchor_count(),
        "anchor_fits": store.fit_count(),
        "derived_geometries": store.count("anchor_fit"),
        "anchor_gate_passed": gate,
    }


def _initial_design(
    suction: np.ndarray,
    pressure: np.ndarray,
    camber_points: np.ndarray,
    features,
    intent: GeometryIntentV2,
) -> np.ndarray:
    beta = np.linspace(0.0, math.pi, len(camber_points))
    parameter = 0.5 * (1.0 - np.cos(beta))
    inlet_local = math.radians(features.inlet_metal_angle_deg - features.stagger_deg)
    outlet_local = math.radians(features.outlet_metal_angle_deg - features.stagger_deg)
    directions = np.array(
        [
            [math.cos(inlet_local), math.sin(inlet_local)],
            [math.cos(outlet_local), math.sin(outlet_local)],
        ]
    )
    basis = _basis_matrix(4, parameter)
    p0 = np.array([0.0, 0.0])
    p3 = np.array([1.0, 0.0])
    fixed = basis[:, [0]] * p0 + basis[:, [3]] * p3
    columns = np.column_stack(
        [
            (basis[:, 1, None] * directions[0]).ravel(),
            (-basis[:, 2, None] * directions[1]).ravel(),
        ]
    )
    target = (camber_points - fixed - basis[:, [2]] * p3).ravel()
    handles = lsq_linear(columns, target, bounds=(0.03, 1.2)).x
    camber = _camber_from_values(inlet_local, outlet_local, handles[0], handles[1])
    camber_eval = CurveEvaluator(camber)
    _, normal = unit_tangent_and_normal(camber_eval, parameter)
    suction_half = np.maximum(1e-6, np.sum((suction - camber_points) * normal, axis=1))
    pressure_half = np.maximum(1e-6, np.sum((camber_points - pressure) * normal, axis=1))
    suction_controls = _fit_thickness_controls(
        parameter,
        suction_half,
        intent.leading_edge_radius_ratio,
        intent.trailing_edge_radius_ratio,
    )
    pressure_controls = _fit_thickness_controls(
        parameter,
        pressure_half,
        intent.leading_edge_radius_ratio,
        intent.trailing_edge_radius_ratio,
    )
    return np.concatenate(
        [
            [inlet_local, outlet_local, handles[0], handles[1]],
            [intent.leading_edge_radius_ratio, intent.trailing_edge_radius_ratio],
            suction_controls[1:-1],
            pressure_controls[1:-1],
        ]
    )


def _spec_from_design(values: np.ndarray, intent: GeometryIntentV2) -> GeometrySpecV2:
    camber = _camber_from_values(values[0], values[1], values[2], values[3])
    edge = EdgeSpec(
        leading_edge_radius_ratio=float(values[4]),
        trailing_edge_radius_ratio=float(values[5]),
    )
    knots = open_uniform_knots(8)
    suction_values = np.concatenate([[values[4]], values[6:12], [values[5]]])
    pressure_values = np.concatenate([[values[4]], values[12:18], [values[5]]])
    return GeometrySpecV2(
        authoring_mode="advanced",
        intent=intent,
        linked_thickness=False,
        camber_curve=camber,
        suction_thickness_curve=BSplineCurveSpec(
            control_points=[[float(value)] for value in suction_values],
            knots=knots,
        ),
        pressure_thickness_curve=BSplineCurveSpec(
            control_points=[[float(value)] for value in pressure_values],
            knots=knots,
        ),
        edge=edge,
    )


def _camber_from_values(
    inlet_local: float,
    outlet_local: float,
    dist_in: float,
    dist_out: float,
) -> BSplineCurveSpec:
    return BSplineCurveSpec(
        control_points=[
            [0.0, 0.0],
            [dist_in * math.cos(inlet_local), dist_in * math.sin(inlet_local)],
            [1.0 - dist_out * math.cos(outlet_local), -dist_out * math.sin(outlet_local)],
            [1.0, 0.0],
        ],
        knots=open_uniform_knots(4),
    )


def _fit_thickness_controls(
    parameter: np.ndarray,
    target: np.ndarray,
    leading: float,
    trailing: float,
) -> np.ndarray:
    basis = _basis_matrix(8, parameter)
    fixed = basis[:, 0] * leading + basis[:, -1] * trailing
    result = lsq_linear(basis[:, 1:-1], target - fixed, bounds=(1e-6, 0.18))
    return np.concatenate([[leading], result.x, [trailing]])


def _basis_matrix(control_count: int, parameter: np.ndarray) -> np.ndarray:
    return np.asarray(
        BSpline(
            open_uniform_knots(control_count),
            np.eye(control_count),
            3,
            extrapolate=False,
        )(parameter)
    )


def _symmetric_distance_metrics(
    source: np.ndarray,
    fitted: np.ndarray,
) -> tuple[float, float, float]:
    pairwise = np.linalg.norm(source[:, None, :] - fitted[None, :, :], axis=2)
    source_nearest = np.min(pairwise, axis=1)
    fitted_nearest = np.min(pairwise, axis=0)
    nearest = np.concatenate([source_nearest, fitted_nearest])
    return (
        float(np.sqrt(np.mean(nearest**2))),
        float(np.max(nearest)),
        float(max(np.max(source_nearest), np.max(fitted_nearest))),
    )


def _project_parameters(
    curve: CurveEvaluator,
    points: np.ndarray,
    initial: np.ndarray,
) -> np.ndarray:
    u = np.clip(np.asarray(initial, dtype=float), 1e-9, 1.0 - 1e-9)
    for _ in range(8):
        value = curve.evaluate(u)
        first = curve.evaluate(u, 1)
        second = curve.evaluate(u, 2)
        delta = value - points
        gradient = np.sum(delta * first, axis=1)
        hessian = np.sum(first * first + delta * second, axis=1)
        step = gradient / np.where(np.abs(hessian) > 1e-12, hessian, 1e-12)
        next_u = np.clip(u - step, 1e-9, 1.0 - 1e-9)
        if float(np.max(np.abs(next_u - u))) <= 1e-12:
            break
        u = next_u
    return u


def _design_bounds(initial: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lower = np.concatenate(
        [
            np.radians([-70.0, -70.0]),
            [0.03, 0.03, 0.0005, 0.0001],
            [1e-6] * 12,
        ]
    )
    upper = np.concatenate(
        [
            np.radians([70.0, 70.0]),
            [1.2, 1.2, 0.03, 0.02],
            [0.18] * 12,
        ]
    )
    return np.minimum(lower, initial), np.maximum(upper, initial)


def _anchor_intent(anchor: AirfoilGeometry, features) -> tuple[GeometryIntentV2, list[str]]:
    warnings = list(anchor.metadata.get("source_warnings", []))
    sign = 1.0 if features.inlet_metal_angle_deg >= 0.0 else -1.0
    inlet = float(np.clip(sign * features.inlet_metal_angle_deg, -84.0, 84.0))
    outlet = float(np.clip(sign * features.outlet_metal_angle_deg, -84.0, 84.0))
    stagger = float(np.clip(sign * features.stagger_deg, -84.0, 84.0))
    tmax = float(np.clip(features.max_thickness_ratio, 0.03, 0.18))
    xt = float(np.clip(features.max_thickness_x_ratio, 0.20, 0.65))
    source_le = float(anchor.metadata.get("leading_edge_radius_ratio", 0.005))
    source_te = float(anchor.metadata.get("trailing_edge_radius_ratio", 0.001))
    leading = float(np.clip(source_le, 0.0005, min(0.03, 0.49 * tmax)))
    trailing = float(np.clip(source_te, 0.0001, min(0.02, 0.49 * tmax)))
    if leading != source_le:
        warnings.append(f"拟合边缘合同将 rLE/c 从 {source_le:.6g} 限制为 {leading:.6g}；原值未修改")
    if trailing != source_te:
        warnings.append(
            f"拟合边缘合同将 rTE/c 从 {source_te:.6g} 限制为 {trailing:.6g}；"
            "原值未修改"
        )
    return (
        GeometryIntentV2(
            inlet_metal_angle_deg=inlet,
            outlet_metal_angle_deg=outlet,
            stagger_deg=stagger,
            max_thickness_ratio=tmax,
            max_thickness_x_ratio=xt,
            leading_edge_radius_ratio=leading,
            trailing_edge_radius_ratio=trailing,
        ),
        warnings,
    )


def _intent_from_design(values: np.ndarray, base: GeometryIntentV2) -> GeometryIntentV2:
    sign = 1.0 if base.handedness.value == "right" else -1.0
    inlet = sign * math.degrees(values[0]) + base.stagger_deg
    outlet = sign * math.degrees(values[1]) + base.stagger_deg
    inlet = float(np.clip(inlet, -84.0, 84.0))
    outlet = float(np.clip(outlet, -84.0, 84.0))
    return base.model_copy(
        update={
            "inlet_metal_angle_deg": inlet,
            "outlet_metal_angle_deg": outlet,
            "camber_le_tangent_ratio": float(values[2]),
            "camber_te_tangent_ratio": float(values[3]),
            "leading_edge_radius_ratio": float(values[4]),
            "trailing_edge_radius_ratio": float(values[5]),
        }
    )


def _refresh_manifest_if_present(store, gate_passed: bool | None = None) -> None:
    manifest_path = store.root / "dataset_manifest.json"
    if not manifest_path.exists():
        return
    manifest = DatasetManifestV2.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    store.write_manifest(
        manifest.model_copy(
            update={
                "anchors": store.anchor_count(),
                "anchor_fits": store.fit_count(),
                "anchor_gate_passed": (
                    store.anchor_gate_passed() if gate_passed is None else gate_passed
                ),
                "files": store.artifact_hashes(),
                "batch_manifests": store.batch_manifests(),
            }
        )
    )
