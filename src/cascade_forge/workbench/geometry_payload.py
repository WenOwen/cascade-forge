from __future__ import annotations

from typing import Any

import numpy as np

from cascade_forge.domain.features import curve_curvature, extract_features
from cascade_forge.domain.labels import geometry_label
from cascade_forge.domain.validation import validate_profile


def _geometry_payload(geometry) -> dict[str, Any]:
    suction, pressure, camber, thickness = geometry.arrays()
    stagger = float(geometry.realized_stagger_deg)
    placed_suction = _rotate_points(suction, stagger)
    placed_pressure = _rotate_points(pressure, stagger)
    placed_camber = _rotate_points(camber, stagger)
    control_points = _rotate_points(
        np.asarray(geometry.spec.camber_curve.control_points, dtype=float), stagger
    )
    leading_circle = _rotate_points(
        np.asarray(geometry.leading_osculating_circle, dtype=float), stagger
    )
    trailing_circle = _rotate_points(
        np.asarray(geometry.trailing_osculating_circle, dtype=float), stagger
    )
    suction_curvature, suction_s = curve_curvature(suction)
    pressure_curvature, pressure_s = curve_curvature(pressure)
    camber_delta = np.gradient(placed_camber, axis=0, edge_order=2)
    angle = np.degrees(np.arctan2(camber_delta[:, 1], camber_delta[:, 0]))
    edge_display = {}
    for edge in ("leading", "trailing"):
        edge_display[edge] = {
            "center": _rotate_points(
                np.asarray([geometry.metadata[f"{edge}_edge_center"]]), stagger
            )[0].tolist(),
            "nose": _rotate_points(np.asarray([geometry.metadata[f"{edge}_edge_nose"]]), stagger)[
                0
            ].tolist(),
            "tangent": _rotate_vectors(
                np.asarray([geometry.metadata[f"{edge}_edge_tangent"]]), stagger
            )[0].tolist(),
        }
    arc_angles = np.radians(np.linspace(0.0, stagger, 48))
    stagger_arc = np.column_stack([0.18 * np.cos(arc_angles), 0.18 * np.sin(arc_angles)])
    return {
        "label": geometry_label(geometry.spec),
        "geometry": geometry.model_dump(mode="json"),
        "features": extract_features(geometry).model_dump(mode="json"),
        "validation": validate_profile(geometry).model_dump(mode="json"),
        "placement": {
            "coordinate_frame": "x-axial,y-positive-tangential",
            "stagger_deg": stagger,
            "suction": placed_suction.tolist(),
            "pressure": placed_pressure.tolist(),
            "camber": placed_camber.tolist(),
            "control_points": control_points.tolist(),
            "leading_osculating_circle": leading_circle.tolist(),
            "trailing_osculating_circle": trailing_circle.tolist(),
            "chord": [[0.0, 0.0], _rotate_points(np.asarray([[1.0, 0.0]]), stagger)[0].tolist()],
            "axial_reference": [[-0.12, 0.0], [1.12, 0.0]],
            "stagger_arc": stagger_arc.tolist(),
            "edges": edge_display,
        },
        "analysis": {
            "camber_x": camber[:, 0].tolist(),
            "thickness": thickness.tolist(),
            "suction_s": suction_s.tolist(),
            "pressure_s": pressure_s.tolist(),
            "suction_curvature": suction_curvature.tolist(),
            "pressure_curvature": pressure_curvature.tolist(),
            "suction_curvature_gradient": np.gradient(
                suction_curvature, suction_s, edge_order=2
            ).tolist(),
            "pressure_curvature_gradient": np.gradient(
                pressure_curvature, pressure_s, edge_order=2
            ).tolist(),
            "angle_deg": angle.tolist(),
        },
    }


def _rotate_points(points: np.ndarray, angle_deg: float) -> np.ndarray:
    angle = np.radians(angle_deg)
    rotation = np.asarray([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    return np.asarray(points, dtype=float) @ rotation.T


def _rotate_vectors(vectors: np.ndarray, angle_deg: float) -> np.ndarray:
    return _rotate_points(vectors, angle_deg)
