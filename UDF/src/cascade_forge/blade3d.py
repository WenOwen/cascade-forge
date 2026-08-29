from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import TypeAlias

import numpy as np

from .models import (
    AirfoilGeometry,
    BladeSection,
    BladeSurface,
    SectionDefinition,
    SectionSchedule,
    payload_sha256,
)

GeometrySource: TypeAlias = (
    Mapping[str, AirfoilGeometry] | Callable[[str], AirfoilGeometry | None]
)


def build_blade(schedule: SectionSchedule, geometry_source: GeometrySource) -> BladeSurface:
    """按 section schedule 构造圆柱坐标三维叶片曲面。"""

    definitions = schedule.sections
    output_count = schedule.output_section_count or len(definitions)
    target_spans = np.linspace(
        definitions[0].span_fraction,
        definitions[-1].span_fraction,
        output_count,
    )
    sections: list[BladeSection] = []
    warnings: list[str] = []

    for span in target_spans:
        lower_index, upper_index, weight = _bracket(definitions, float(span))
        lower_definition = definitions[lower_index]
        upper_definition = definitions[upper_index]
        lower_geometry = _resolve(geometry_source, lower_definition.geometry_id)
        upper_geometry = _resolve(geometry_source, upper_definition.geometry_id)
        definition = _interpolate_definition(
            lower_definition, upper_definition, weight, float(span)
        )
        suction, pressure, geometry_id, interpolated = _interpolate_geometry(
            lower_geometry, upper_geometry, weight
        )
        suction_xyz = _place_surface(suction, definition, schedule.rotation_direction)
        pressure_xyz = _place_surface(pressure, definition, schedule.rotation_direction)
        sections.append(
            BladeSection(
                span_fraction=float(span),
                radius_m=definition.radius_m,
                suction_xyz=suction_xyz.tolist(),
                pressure_xyz=pressure_xyz.tolist(),
                geometry_id=geometry_id,
                interpolated=interpolated,
            )
        )
        if interpolated:
            warnings.append(
                f"span={span:.6f} 为 {lower_geometry.geometry_id} 与 "
                f"{upper_geometry.geometry_id} 的线性坐标插值"
            )

    blade_id = "blade_" + payload_sha256(
        {
            "schedule": schedule.model_dump(mode="json"),
            "sections": [section.geometry_id for section in sections],
        }
    )[:24]
    return BladeSurface(
        blade_id=blade_id,
        schedule=schedule,
        sections=sections,
        warnings=warnings,
    )


def _resolve(source: GeometrySource, geometry_id: str) -> AirfoilGeometry:
    geometry = source.get(geometry_id) if isinstance(source, Mapping) else source(geometry_id)
    if geometry is None:
        raise KeyError(f"找不到 Geometry_ID: {geometry_id}")
    return geometry


def _bracket(
    definitions: list[SectionDefinition], span: float
) -> tuple[int, int, float]:
    spans = np.asarray([definition.span_fraction for definition in definitions])
    if span <= spans[0] + 1e-14:
        return 0, 0, 0.0
    if span >= spans[-1] - 1e-14:
        index = len(definitions) - 1
        return index, index, 0.0
    upper = int(np.searchsorted(spans, span, side="right"))
    lower = upper - 1
    weight = (span - spans[lower]) / (spans[upper] - spans[lower])
    if abs(weight) < 1e-12:
        return lower, lower, 0.0
    if abs(weight - 1.0) < 1e-12:
        return upper, upper, 0.0
    return lower, upper, float(weight)


def _interpolate_definition(
    lower: SectionDefinition,
    upper: SectionDefinition,
    weight: float,
    span: float,
) -> SectionDefinition:
    if lower is upper or weight == 0.0:
        return lower.model_copy(update={"span_fraction": span})

    def blend(name: str) -> float:
        return (1.0 - weight) * float(getattr(lower, name)) + weight * float(
            getattr(upper, name)
        )

    return SectionDefinition(
        span_fraction=span,
        radius_m=blend("radius_m"),
        chord_m=blend("chord_m"),
        geometry_id=f"interp({lower.geometry_id},{upper.geometry_id},{weight:.12f})",
        stagger_deg=blend("stagger_deg"),
        axial_offset_m=blend("axial_offset_m"),
        tangential_offset_m=blend("tangential_offset_m"),
        lean_m=blend("lean_m"),
        sweep_m=blend("sweep_m"),
    )


def _interpolate_geometry(
    lower: AirfoilGeometry,
    upper: AirfoilGeometry,
    weight: float,
) -> tuple[np.ndarray, np.ndarray, str, bool]:
    lower_suction, lower_pressure, _, _ = lower.arrays()
    if lower.geometry_id == upper.geometry_id or weight == 0.0:
        return lower_suction, lower_pressure, lower.geometry_id, False
    upper_suction, upper_pressure, _, _ = upper.arrays()
    if lower_suction.shape != upper_suction.shape or lower_pressure.shape != upper_pressure.shape:
        raise ValueError("展向插值要求所有二维叶型具有相同点数")
    suction = (1.0 - weight) * lower_suction + weight * upper_suction
    pressure = (1.0 - weight) * lower_pressure + weight * upper_pressure
    geometry_id = f"interp({lower.geometry_id},{upper.geometry_id},{weight:.12f})"
    return suction, pressure, geometry_id, True


def _place_surface(
    points: np.ndarray,
    definition: SectionDefinition,
    rotation_direction: str,
) -> np.ndarray:
    chord = definition.chord_m
    angle = math.radians(definition.stagger_deg)
    x_local = points[:, 0] * chord
    y_local = points[:, 1] * chord
    axial = (
        x_local * math.cos(angle)
        - y_local * math.sin(angle)
        + definition.axial_offset_m
        + definition.sweep_m
    )
    tangential = (
        x_local * math.sin(angle)
        + y_local * math.cos(angle)
        + definition.tangential_offset_m
        + definition.lean_m
    )
    direction = 1.0 if rotation_direction == "ccw" else -1.0
    theta = direction * tangential / definition.radius_m
    radial_x = definition.radius_m * np.cos(theta)
    radial_y = definition.radius_m * np.sin(theta)
    return np.column_stack([radial_x, radial_y, axial])
