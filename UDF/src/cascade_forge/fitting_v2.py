from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .curves import CurveEvaluator, open_uniform_knots
from .features import extract_features
from .geometry_v2 import _resolve_thickness, generate_profile_v2
from .legacy import ROWS, _anchor_geometry
from .models import AirfoilGeometry, file_sha256
from .models_v2 import (
    AirfoilGeometryV2,
    AnchorFitRecord,
    BezierCurveSpec,
    BSplineCurveSpec,
    CurveFamily,
    DatasetManifestV2,
    EdgeSpec,
    GeometryIntentV2,
    GeometrySpecV2,
    NURBSCurveSpec,
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
                _anchor_geometry(
                    row_name,
                    section,
                    source_path,
                    file_sha256(source_path),
                )
            )
    if len(anchors) != 48:
        raise ValueError(f"NASA 锚点数量应为 48，实际为 {len(anchors)}")
    return anchors


def fit_anchor_geometry(
    anchor: AirfoilGeometry,
    camber_family: CurveFamily,
    thickness_family: CurveFamily,
) -> tuple[AnchorFitRecord, AirfoilGeometryV2]:
    """把一个不可变原始锚点分解拟合为指定 v2 曲线族组合。"""

    suction, pressure, camber, thickness = anchor.arrays()
    parameter = np.asarray(camber[:, 0], dtype=float)
    camber_curve = _fit_camber_curve(camber_family, parameter, camber)
    dx = np.gradient(camber[:, 0], parameter, edge_order=2)
    dy = np.gradient(camber[:, 1], parameter, edge_order=2)
    normal_y = dx / np.maximum(np.hypot(dx, dy), 1e-14)
    normal_half_thickness = np.maximum(0.0, 0.5 * thickness * normal_y)
    thickness_curve, _ = _resolve_thickness(thickness_family, parameter, normal_half_thickness)
    source_features = extract_features(anchor)
    intent, source_warnings = _anchor_intent(anchor, source_features)
    edge = EdgeSpec(
        leading_edge_radius_ratio=intent.leading_edge_radius_ratio,
        trailing_edge_radius_ratio=intent.trailing_edge_radius_ratio,
        trailing_edge_half_wedge_deg=intent.trailing_edge_half_wedge_deg,
    )
    spec = GeometrySpecV2(
        authoring_mode="advanced",
        intent=intent,
        camber_family=camber_family,
        thickness_family=thickness_family,
        camber_curve=camber_curve,
        suction_thickness_curve=thickness_curve,
        pressure_thickness_curve=thickness_curve,
        edge=edge,
    )
    fitted = generate_profile_v2(spec).model_copy(
        update={"origin": "anchor_fit", "source_id": anchor.geometry_id}
    )
    fitted_suction, fitted_pressure, _, _ = fitted.arrays()
    source_points = np.vstack([suction, pressure])
    fitted_points = np.vstack([fitted_suction, fitted_pressure])
    pairwise = np.linalg.norm(source_points[:, None, :] - fitted_points[None, :, :], axis=2)
    nearest = np.concatenate([np.min(pairwise, axis=0), np.min(pairwise, axis=1)])
    hausdorff = max(
        float(np.max(np.min(pairwise, axis=0))),
        float(np.max(np.min(pairwise, axis=1))),
    )
    rms = float(np.sqrt(np.mean(nearest**2)))
    maximum = float(np.max(nearest))
    status = (
        "valid"
        if rms <= 5e-4 and maximum <= 2e-3
        else "warning"
        if rms <= 1.5e-3 and maximum <= 5e-3
        else "rejected"
    )
    fit_key = (
        f"{anchor.geometry_id}:{camber_family.value}:{thickness_family.value}:{fitted.geometry_id}"
    )
    record = AnchorFitRecord(
        fit_id="fit_v2_" + hashlib.sha256(fit_key.encode()).hexdigest()[:24],
        source_anchor_id=anchor.geometry_id,
        geometry_id=fitted.geometry_id,
        camber_family=camber_family,
        thickness_family=thickness_family,
        rms_distance_ratio=rms,
        max_distance_ratio=maximum,
        hausdorff_approx_ratio=hausdorff,
        fit_status=status,
        source_warnings=source_warnings,
        explicit_spec=spec.model_dump(mode="json"),
    )
    return record, fitted


def _fit_camber_curve(family: CurveFamily, parameter: np.ndarray, target: np.ndarray):
    """以固定端点的线性最小二乘拟合锚点中弧线。"""

    if family == CurveFamily.BEZIER:
        count = 4
        template = BezierCurveSpec(degree=3, control_points=[[0.0, 0.0]] * count)

        def make(points):
            return BezierCurveSpec(degree=3, control_points=points)

    else:
        count = 6
        knots = open_uniform_knots(count, 3)
        weights = [1.0, 0.92, 1.08, 1.12, 0.95, 1.0] if family == CurveFamily.NURBS else None
        template = (
            BSplineCurveSpec(
                degree=3,
                control_points=[[0.0, 0.0]] * count,
                knots=knots,
            )
            if weights is None
            else NURBSCurveSpec(
                degree=3,
                control_points=[[0.0, 0.0]] * count,
                knots=knots,
                weights=weights,
            )
        )

        def make(points):
            if weights is None:
                return BSplineCurveSpec(degree=3, control_points=points, knots=knots)
            return NURBSCurveSpec(
                degree=3,
                control_points=points,
                knots=knots,
                weights=weights,
            )

    columns = []
    for column in range(count):
        basis_points = [
            [
                1.0 if row == column else 0.0,
                1.0 if row == column else 0.0,
            ]
            for row in range(count)
        ]
        basis_curve = template.model_copy(update={"control_points": basis_points})
        columns.append(CurveEvaluator(basis_curve).evaluate(parameter)[:, 0])
    basis = np.column_stack(columns)
    controls = np.zeros((count, 2), dtype=float)
    controls[0] = target[0]
    controls[-1] = target[-1]
    residual_target = target - basis[:, [0]] * controls[0] - basis[:, [-1]] * controls[-1]
    controls[1:-1] = np.linalg.lstsq(basis[:, 1:-1], residual_target, rcond=None)[0]
    return make(controls.tolist())


def fit_legacy_anchors(
    raw_root: str | Path,
) -> tuple[list[AnchorFitRecord], list[AirfoilGeometryV2]]:
    """为 48 个锚点生成九种分解，固定返回 432 个可追溯派生记录。"""

    records: list[AnchorFitRecord] = []
    geometries: list[AirfoilGeometryV2] = []
    for anchor in load_legacy_anchor_geometries(raw_root):
        for camber_family in CurveFamily:
            for thickness_family in CurveFamily:
                record, geometry = fit_anchor_geometry(anchor, camber_family, thickness_family)
                records.append(record)
                geometries.append(geometry)
    if len(records) != 432:
        raise AssertionError(f"锚点派生拟合应为 432，实际为 {len(records)}")
    return records, geometries


def fit_anchors_to_dataset(
    raw_root: str | Path,
    dataset_root: str | Path,
    *,
    progress: Callable[[dict[str, int]], None] | None = None,
) -> dict[str, int]:
    """把 48 个只读锚点和 432 个派生拟合写入独立 v2 分区。"""

    from .storage_v2 import DatasetStoreV2
    from .validation import validate_profile

    store = DatasetStoreV2(dataset_root)
    anchors = load_legacy_anchor_geometries(raw_root)
    if store.anchor_count() == 0:
        store.append_anchors(anchors)
    if store.fit_count() == 432 and store.count("anchor_fit") == 432:
        _refresh_manifest_if_present(store)
        return {
            "anchors": store.anchor_count(),
            "anchor_fits": store.fit_count(),
            "derived_geometries": store.count("anchor_fit"),
        }
    existing_fit_ids = store.fit_ids()
    existing_geometry_ids = store.geometry_ids()
    fit_records: list[AnchorFitRecord] = []
    geometry_rows = []
    completed = 0
    for anchor in anchors:
        for camber_family in CurveFamily:
            for thickness_family in CurveFamily:
                record, geometry = fit_anchor_geometry(anchor, camber_family, thickness_family)
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
                            {
                                "intent_group_index": None,
                                "proposal_index": None,
                                "is_extra": False,
                            },
                        )
                    )
                    existing_geometry_ids.add(geometry.geometry_id)
                completed += 1
                if len(fit_records) >= 48 or len(geometry_rows) >= 48:
                    store.append_anchor_fits(fit_records)
                    store.append_geometries(
                        geometry_rows,
                        batch_metadata={
                            "kind": "anchor_fit",
                            "completed": completed,
                        },
                    )
                    fit_records.clear()
                    geometry_rows.clear()
                if progress and completed % 9 == 0:
                    progress({"completed": completed, "total": 432})
    store.append_anchor_fits(fit_records)
    store.append_geometries(
        geometry_rows,
        batch_metadata={"kind": "anchor_fit", "completed": completed},
    )
    _refresh_manifest_if_present(store)
    return {
        "anchors": store.anchor_count(),
        "anchor_fits": store.fit_count(),
        "derived_geometries": store.count("anchor_fit"),
    }


def _refresh_manifest_if_present(store) -> None:
    """拟合分区落盘后刷新正式 manifest 的计数和不可变文件哈希。"""

    manifest_path = store.root / "dataset_manifest.json"
    if not manifest_path.exists():
        return
    manifest = DatasetManifestV2.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    store.write_manifest(
        manifest.model_copy(
            update={
                "anchors": store.anchor_count(),
                "anchor_fits": store.fit_count(),
                "files": store.artifact_hashes(),
                "batch_manifests": store.batch_manifests(),
            }
        )
    )


def _anchor_intent(anchor: AirfoilGeometry, features) -> tuple[GeometryIntentV2, list[str]]:
    warnings = list(anchor.metadata.get("source_warnings", []))
    inlet = float(np.clip(abs(features.inlet_metal_angle_deg), 20.0, 75.0))
    turning = float(np.clip(abs(features.turning_angle_deg), 0.0, min(65.0, inlet + 20.0)))
    if not -20.0 <= inlet - turning <= 65.0:
        turning = float(np.clip(inlet - np.clip(inlet - turning, -20.0, 65.0), 0.0, 65.0))
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
            f"拟合边缘合同将 rTE/c 从 {source_te:.6g} 限制为 {trailing:.6g}；原值未修改"
        )
    return (
        GeometryIntentV2(
            inlet_metal_angle_deg=inlet,
            turning_angle_deg=turning,
            max_thickness_ratio=tmax,
            max_thickness_x_ratio=xt,
            leading_edge_radius_ratio=leading,
            trailing_edge_radius_ratio=trailing,
            trailing_edge_half_wedge_deg=float(
                np.clip(features.trailing_edge_half_wedge_deg, 0.0, 25.0)
            ),
        ),
        warnings,
    )
