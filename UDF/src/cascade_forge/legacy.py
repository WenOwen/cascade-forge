from __future__ import annotations

import hashlib
import json
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.interpolate import PchipInterpolator

from .features import extract_features
from .models import (
    AirfoilGeometry,
    SectionDefinition,
    SectionSchedule,
    file_sha256,
)
from .storage import DatasetStore
from .validation import validate_profile

ROWS = ("R1", "R2", "S1", "S2")


def ingest_legacy_anchors(
    source: str | Path,
    output_root: str | Path,
    *,
    raw_destination: str | Path | None = None,
) -> dict[str, Any]:
    source = Path(source).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    store = DatasetStore(output_root)
    destination = (
        Path(raw_destination).resolve()
        if raw_destination
        else Path(__file__).resolve().parents[2] / "data" / "raw" / "nasa120859"
    )
    destination.mkdir(parents=True, exist_ok=True)
    existing = store.geometry_ids()
    records = []
    manifest_entries: list[dict[str, Any]] = []

    for row_name in ROWS:
        row_source = source / row_name
        metadata_path = row_source / "section_metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        row_destination = destination / row_name
        row_destination.mkdir(parents=True, exist_ok=True)
        copied_metadata = row_destination / "section_metadata.json"
        shutil.copy2(metadata_path, copied_metadata)

        for section in metadata["sections"]:
            filename = metadata["filename_pattern"].format(suffix=section["suffix"])
            source_path = row_source / filename
            copied_path = row_destination / filename
            shutil.copy2(source_path, copied_path)
            source_hash = file_sha256(source_path)
            geometry = _anchor_geometry(row_name, section, source_path, source_hash)
            report = validate_profile(geometry)
            features = extract_features(geometry)
            if geometry.geometry_id not in existing:
                records.append((geometry, features, report))
                existing.add(geometry.geometry_id)
            manifest_entries.append(
                {
                    "geometry_id": geometry.geometry_id,
                    "blade_row": row_name,
                    "section": section["suffix"],
                    "source_path": str(source_path),
                    "copied_path": str(copied_path),
                    "sha256": source_hash,
                    "validation_status": report.status.value,
                    "warnings": [issue.message for issue in report.issues],
                }
            )

    store.append_geometries(records)
    manifest = {
        "schema_version": "1.0.0",
        "source_root": str(source),
        "destination_root": str(destination),
        "created_at": datetime.now(UTC).isoformat(),
        "count": len(manifest_entries),
        "entries": manifest_entries,
    }
    manifest_path = destination / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def legacy_section_schedule(
    raw_root: str | Path,
    row_name: str,
    *,
    output_section_count: int | None = None,
) -> SectionSchedule:
    """从精选 NASA 元数据构造可追溯的三维 section schedule。"""

    raw_root = Path(raw_root).resolve()
    row_name = row_name.upper()
    if row_name not in ROWS:
        raise ValueError(f"未知叶排: {row_name}")
    row_root = raw_root / row_name
    metadata = json.loads((row_root / "section_metadata.json").read_text(encoding="utf-8"))
    ref_hub = float(metadata["ref_hub_r"])
    ref_tip = float(metadata["ref_tip_r"])
    sections = []
    for section in metadata["sections"]:
        filename = metadata["filename_pattern"].format(suffix=section["suffix"])
        source_hash = file_sha256(row_root / filename)
        geometry_id = "geo_anchor_" + hashlib.sha256(
            f"{row_name}:{section['suffix']}:{source_hash}".encode()
        ).hexdigest()[:20]
        span = (float(section["radius_in"]) - ref_hub) / (ref_tip - ref_hub)
        sections.append(
            SectionDefinition(
                span_fraction=span,
                radius_m=float(section["radius_in"]) * 0.0254,
                chord_m=float(section["chord_in"]) * 0.0254,
                geometry_id=geometry_id,
                stagger_deg=float(section["gamma_deg"]),
                axial_offset_m=-float(section["zcsl_in"]) * 0.0254,
                tangential_offset_m=-float(section["ycsl_in"]) * 0.0254,
            )
        )
    return SectionSchedule(
        name=f"NASA120859-{row_name}",
        blade_count=int(metadata["num_of_blades"]),
        sections=sections,
        output_section_count=output_section_count,
    )


def _anchor_geometry(
    row_name: str,
    section: dict[str, Any],
    source_path: Path,
    source_hash: str,
    points: int = 201,
) -> AirfoilGeometry:
    values = _read_numeric_rows(source_path)
    chord = float(section["chord_in"])
    x = values[:, 0] / chord
    pressure_y = values[:, 1] / chord
    suction_y = values[:, 2] / chord
    midpoint = 0.5 * (pressure_y + suction_y)

    # 用前/尾缘中点定义规范弦线；原始文件完整保留，因此变换是可追溯的。
    origin = np.array([x[0], midpoint[0]])
    chord_vector = np.array([x[-1], midpoint[-1]]) - origin
    angle = math.atan2(chord_vector[1], chord_vector[0])
    scale = float(np.linalg.norm(chord_vector))
    cosine = math.cos(-angle)
    sine = math.sin(-angle)

    def transform(y: np.ndarray) -> np.ndarray:
        dx = x - origin[0]
        dy = y - origin[1]
        return np.column_stack(
            [(dx * cosine - dy * sine) / scale, (dx * sine + dy * cosine) / scale]
        )

    pressure_raw = transform(pressure_y)
    suction_raw = transform(suction_y)
    pressure_raw = pressure_raw[np.argsort(pressure_raw[:, 0])]
    suction_raw = suction_raw[np.argsort(suction_raw[:, 0])]
    target = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, points)))
    pressure = np.column_stack(
        [target, PchipInterpolator(pressure_raw[:, 0], pressure_raw[:, 1])(target)]
    )
    suction = np.column_stack(
        [target, PchipInterpolator(suction_raw[:, 0], suction_raw[:, 1])(target)]
    )
    leading_mid = 0.5 * (pressure[0, 1] + suction[0, 1])
    pressure[0, 1] = suction[0, 1] = leading_mid
    camber = 0.5 * (pressure + suction)
    camber[:, 0] = target
    chord_line = np.linspace(camber[0, 1], camber[-1, 1], points)
    camber[:, 1] -= chord_line
    pressure[:, 1] -= chord_line
    suction[:, 1] -= chord_line
    camber[0, 1] = camber[-1, 1] = 0.0
    thickness = suction[:, 1] - pressure[:, 1]

    warnings: list[str] = []
    rte_ratio = float(section["rte"]) / chord
    if rte_ratio > 0.02:
        warnings.append(
            f"可疑尾缘半径：rte/c={rte_ratio:.6f}；保留原始值，未静默修正"
        )
    tmax = float(np.max(thickness))
    xt = float(target[int(np.argmax(thickness))])
    out_of_domain = not (0.02 <= tmax <= 0.15 and 0.20 <= xt <= 0.65)
    geometry_id = "geo_anchor_" + hashlib.sha256(
        f"{row_name}:{section['suffix']}:{source_hash}".encode()
    ).hexdigest()[:20]
    return AirfoilGeometry(
        geometry_id=geometry_id,
        origin="anchor",
        source_id=f"NASA120859/{row_name}/{section['suffix']}",
        suction=suction.tolist(),
        pressure=pressure.tolist(),
        camber=camber.tolist(),
        thickness=thickness.tolist(),
        realized_stagger_deg=float(section["gamma_deg"]),
        metadata={
            "source_sha256": source_hash,
            "source_filename": source_path.name,
            "blade_row": row_name,
            "section_suffix": section["suffix"],
            "chord_in": chord,
            "leading_edge_radius_ratio": float(section["rle"]) / chord,
            "trailing_edge_radius_ratio": rte_ratio,
            "out_of_generation_domain": out_of_domain,
            "source_warnings": warnings,
            "canonicalization": "translate/rotate/scale by endpoint midpoints; PCHIP resample",
        },
    )


def _read_numeric_rows(path: Path) -> np.ndarray:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        try:
            rows.append([float(fields[0]), float(fields[1]), float(fields[2])])
        except ValueError:
            continue
    if len(rows) < 4:
        raise ValueError(f"截面数据点不足: {path}")
    return np.asarray(rows)
