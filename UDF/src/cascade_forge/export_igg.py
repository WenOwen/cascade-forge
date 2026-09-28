"""IGG curve data files: name, XYZ, point count, then one XYZ point per line.

NUMECA IGG User Manual, File Formats / Curve data files. A DAT file contains
one curve record. XYZ selects an interpolating C-spline, not a polyline.
"""

from __future__ import annotations

import io
import re
from typing import Literal
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import numpy as np


def naca65_igg_dat(
    case: dict,
    source: Literal["native", "fitted"] = "native",
    curve: Literal["closed", "suction", "pressure"] = "closed",
) -> str:
    """Export a closed TE -> SS -> LE -> PS -> TE curve in the XY plane (c=1).

    Use the complete normalized/rounded contour, never the unrounded raw data.
    Keep the closing TE point, and include the shared LE only once.
    """
    if source == "native":
        geometry = case["reference"]
        sides = (geometry["normalized_suction"], geometry["normalized_pressure"])
        identity = geometry["native_id"]
    elif source == "fitted":
        geometry = case.get("fitted_geometry")
        if geometry is None:
            raise ValueError("没有拟合轮廓，请先生成并拟合，或选择原生轮廓")
        sides = (geometry["suction"], geometry["pressure"])
        identity = geometry["geometry_id"]
    else:
        raise ValueError("DAT source 仅支持 native 或 fitted")
    prefix = re.sub(r"[^A-Za-z0-9_]", "_", str(identity))[:80] or "naca65"
    surfaces = []
    for values in sides:
        points = np.asarray(values, dtype=float)
        if points.ndim != 2 or points.shape[1] != 2 or len(points) < 2:
            raise ValueError("IGG 曲线至少需要两个二维坐标点")
        if not np.isfinite(points).all():
            raise ValueError("IGG 坐标不能包含 NaN 或无穷值")
        if np.any(np.linalg.norm(np.diff(points, axis=0), axis=1) <= 1e-15):
            raise ValueError("IGG 曲线不能包含连续重复点")
        surfaces.append(points)
    suction, pressure = surfaces
    if not np.allclose(suction[[0, -1]], pressure[[0, -1]], rtol=0, atol=1e-12):
        raise ValueError("IGG 叶型导出需要两侧前缘和尾缘端点闭合")
    # These arrays already identify the LE. Never split by minimum x, nor by
    # a hard-coded index in an independently sampled closed outline.
    if curve == "closed":
        outline = np.vstack([suction[::-1], pressure[1:]])
        outline[-1] = outline[0]
        suffix = "profile"
    elif curve == "suction":
        outline, suffix = suction[::-1], "Suction"
    elif curve == "pressure":
        outline, suffix = pressure, "Pressure"
    else:
        raise ValueError("DAT curve 仅支持 closed、suction 或 pressure")
    lines = [f"{prefix}_{source}_{suffix}", "XYZ", str(len(outline))]
    lines.extend(f"{x:.16e} {y:.16e} 0.0000000000000000e+00" for x, y in outline)
    return "\n".join(lines) + "\n"


def naca65_igg_split_zip(case: dict, source: Literal["native", "fitted"] = "native") -> bytes:
    """Two independent single-curve DAT files, sharing the exact defined LE/TE."""
    designation = case["reference"].get("designation", "NACA 65")
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", designation.removeprefix("NACA ").split()[0])[:80]
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for curve in ("suction", "pressure"):
            content = naca65_igg_dat(case, source, curve)
            entry = ZipInfo(f"{stem}-{source}-{curve}.dat")
            entry.compress_type = ZIP_DEFLATED
            archive.writestr(entry, content.encode("ascii"))
    return output.getvalue()
