from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from .models import BladeSurface, file_sha256


def export_geomturbo(blade: BladeSurface, destination: str | Path) -> Path:
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    schedule = blade.schedule
    lines = [
        "GEOMETRY TURBO VERSION 3.7",
        "geometric_tolerance       1e-006",
        "blade_expansion_factor_hub     5.9999999E-02",
        "blade_expansion_factor_shroud  5.9999999E-02",
        "blade_merging             0",
        "units                     1",
        f"number_of_blades          {schedule.blade_count}",
        "data_reduction            0",
        "data_reduction_spacing_tolerance      0.0001",
        "data_reduction_angle_tolerance        90",
        "hub",
    ]
    if schedule.hub_curve_path:
        lines.extend(["EXTERNAL", Path(schedule.hub_curve_path).name])
    else:
        lines.extend(["CONSTANT", f"{blade.sections[0].radius_m:.12E}"])
    lines.append("shroud")
    if schedule.shroud_curve_path:
        lines.extend(["EXTERNAL", Path(schedule.shroud_curve_path).name])
    else:
        lines.extend(["CONSTANT", f"{blade.sections[-1].radius_m:.12E}"])

    for side in ("suction", "pressure"):
        lines.extend([side, "SECTIONAL", str(len(blade.sections))])
        for index, section in enumerate(blade.sections, start=1):
            coordinates = np.asarray(getattr(section, f"{side}_xyz"), dtype=float)
            lines.extend([f"#SECTION  {index}", "XYZ", str(len(coordinates))])
            lines.extend(
                f"{x:.12E} {y:.12E} {z:.12E}" for x, y, z in coordinates
            )
    destination.write_text("\n".join(lines) + "\n", encoding="ascii")

    manifest = {
        "schema_version": "1.0.0",
        "blade_id": blade.blade_id,
        "geomturbo_path": str(destination),
        "geomturbo_sha256": file_sha256(destination),
        "schedule": schedule.model_dump(mode="json"),
        "warnings": blade.warnings,
    }
    destination.with_suffix(destination.suffix + ".manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destination


def export_blade_csv(blade: BladeSurface, destination: str | Path) -> Path:
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["side", "section", "span_fraction", "point", "x_m", "y_m", "z_m"])
        for section_index, section in enumerate(blade.sections):
            for side in ("suction", "pressure"):
                for point_index, coordinate in enumerate(getattr(section, f"{side}_xyz")):
                    writer.writerow(
                        [side, section_index, section.span_fraction, point_index, *coordinate]
                    )
    return destination


def parse_geomturbo(path: str | Path) -> dict[str, object]:
    """解析本项目写出的 SECTIONAL GeomTurbo，用于往返验证。"""

    lines = [line.strip() for line in Path(path).read_text(encoding="ascii").splitlines()]
    result: dict[str, object] = {"suction": [], "pressure": []}
    for line in lines:
        if line.startswith("number_of_blades"):
            result["blade_count"] = int(line.split()[-1])
    for side in ("suction", "pressure"):
        index = lines.index(side)
        if lines[index + 1] != "SECTIONAL":
            raise ValueError(f"{side} 不是 SECTIONAL 数据")
        section_count = int(lines[index + 2])
        cursor = index + 3
        sections = []
        for _ in range(section_count):
            if not lines[cursor].startswith("#SECTION") or lines[cursor + 1] != "XYZ":
                raise ValueError("GeomTurbo 截面标记异常")
            point_count = int(lines[cursor + 2])
            start = cursor + 3
            coordinates = np.array(
                [
                    [float(value) for value in lines[row].split()]
                    for row in range(start, start + point_count)
                ]
            )
            sections.append(coordinates)
            cursor = start + point_count
        result[side] = sections
    return result
