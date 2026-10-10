from __future__ import annotations

import numpy as np

from cascade_forge.domain.models import (
    AirfoilGeometryV2,
    GeometryFeaturesV2,
)


def curve_curvature(points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    delta = np.diff(points, axis=0)
    segment = np.linalg.norm(delta, axis=1)
    s = np.concatenate([[0.0], np.cumsum(segment)])
    s = np.maximum.accumulate(s + np.arange(len(s)) * 1e-15)
    dx = np.gradient(points[:, 0], s, edge_order=2)
    dy = np.gradient(points[:, 1], s, edge_order=2)
    ddx = np.gradient(dx, s, edge_order=2)
    ddy = np.gradient(dy, s, edge_order=2)
    denominator = np.maximum((dx * dx + dy * dy) ** 1.5, 1e-14)
    curvature = (dx * ddy - dy * ddx) / denominator
    return curvature, s


def _signed_area(suction: np.ndarray, pressure: np.ndarray) -> float:
    polygon = np.vstack([suction, pressure[::-1]])
    x = polygon[:, 0]
    y = polygon[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - y * np.roll(x, -1)))


def extract_features(geometry: AirfoilGeometryV2) -> GeometryFeaturesV2:
    suction, pressure, camber, thickness = geometry.arrays()
    max_index = int(np.argmax(thickness))
    camber_abs = np.abs(camber[:, 1])
    camber_index = int(np.argmax(camber_abs))
    suction_curvature, suction_s = curve_curvature(suction)
    pressure_curvature, pressure_s = curve_curvature(pressure)
    suction_gradient = np.gradient(suction_curvature, suction_s, edge_order=2)
    pressure_gradient = np.gradient(pressure_curvature, pressure_s, edge_order=2)
    margin = max(6, int(0.05 * len(suction)))

    def interior_max(values: np.ndarray) -> float:
        return float(np.max(np.abs(values[margin:-margin])))

    from cascade_forge.domain.curves import CurveEvaluator

    tangent = CurveEvaluator(geometry.spec.camber_curve).evaluate([0.0, 1.0], 1)
    local_angles = np.degrees(np.arctan2(tangent[:, 1], tangent[:, 0]))
    inlet_local, outlet_local = (round(float(angle), 12) for angle in local_angles)
    inlet_axial = inlet_local + geometry.realized_stagger_deg
    outlet_axial = outlet_local + geometry.realized_stagger_deg
    inlet_tangential = 90.0 - inlet_axial
    outlet_tangential = 90.0 - outlet_axial
    outline = np.vstack([suction, pressure[-2:0:-1]])
    return GeometryFeaturesV2(
        geometry_id=geometry.geometry_id,
        intent_id=geometry.intent_id,
        inlet_metal_angle_deg=inlet_axial,
        outlet_metal_angle_deg=outlet_axial,
        inlet_metal_angle_axial_deg=inlet_axial,
        outlet_metal_angle_axial_deg=outlet_axial,
        inlet_metal_angle_tangential_deg=inlet_tangential,
        outlet_metal_angle_tangential_deg=outlet_tangential,
        inlet_local_angle_deg=inlet_local,
        outlet_local_angle_deg=outlet_local,
        metal_angle_difference_deg=inlet_axial - outlet_axial,
        stagger_deg=geometry.realized_stagger_deg,
        max_thickness_ratio=float(thickness[max_index]),
        max_thickness_x_ratio=float(camber[max_index, 0]),
        max_camber_ratio=float(camber_abs[camber_index]),
        max_camber_x_ratio=float(camber[camber_index, 0]),
        leading_edge_radius_ratio=geometry.edge_metrics["leading_edge_radius_ratio"],
        trailing_edge_radius_ratio=geometry.edge_metrics["trailing_edge_radius_ratio"],
        suction_max_abs_curvature=interior_max(suction_curvature),
        pressure_max_abs_curvature=interior_max(pressure_curvature),
        max_abs_curvature_gradient=max(
            interior_max(suction_gradient), interior_max(pressure_gradient)
        ),
        leading_edge_max_abs_curvature=float(
            1.0 / geometry.edge_metrics["leading_edge_radius_ratio"]
        ),
        trailing_edge_max_abs_curvature=float(
            1.0 / geometry.edge_metrics["trailing_edge_radius_ratio"]
        ),
        signed_area=_signed_area(suction, pressure),
        full_outline_self_intersects=_polyline_self_intersects(outline, closed=True),
    )


def _polyline_self_intersects(points: np.ndarray, *, closed: bool) -> bool:
    """检查完整轮廓的非相邻线段相交；共端点的相邻线段不算自交。"""

    if closed:
        points = np.vstack([points, points[0]])
    starts = points[:-1]
    ends = points[1:]
    segment_count = len(starts)
    first, second = np.triu_indices(segment_count, k=2)
    if closed:
        keep = ~((first == 0) & (second == segment_count - 1))
        first, second = first[keep], second[keep]
    a, b = starts[first], ends[first]
    c, d = starts[second], ends[second]
    bbox = (
        (np.maximum(a[:, 0], b[:, 0]) >= np.minimum(c[:, 0], d[:, 0]) - 1e-13)
        & (np.maximum(c[:, 0], d[:, 0]) >= np.minimum(a[:, 0], b[:, 0]) - 1e-13)
        & (np.maximum(a[:, 1], b[:, 1]) >= np.minimum(c[:, 1], d[:, 1]) - 1e-13)
        & (np.maximum(c[:, 1], d[:, 1]) >= np.minimum(a[:, 1], b[:, 1]) - 1e-13)
    )
    if not np.any(bbox):
        return False
    a, b, c, d = a[bbox], b[bbox], c[bbox], d[bbox]

    def orientation(p: np.ndarray, q: np.ndarray, r: np.ndarray) -> np.ndarray:
        pq = q - p
        pr = r - p
        return pq[:, 0] * pr[:, 1] - pq[:, 1] * pr[:, 0]

    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)
    return bool(np.any((o1 * o2 < -1e-24) & (o3 * o4 < -1e-24)))
