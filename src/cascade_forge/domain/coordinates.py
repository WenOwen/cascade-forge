"""Additive native profile dataset; existing spline datasets remain untouched."""

from __future__ import annotations

import csv
import io
from typing import Any

import numpy as np


def coordinate_text(record: dict[str, Any], format: str) -> str:
    stream = io.StringIO()
    if format == "csv":
        writer = csv.writer(stream)
        writer.writerow(
            [
                "side",
                "point",
                *(["x", "y"] if record.get("family") == "imported" else ["x_over_c", "y_over_c"]),
            ]
        )
        for side in ("suction", "pressure"):
            for index, point in enumerate(record[side]):
                writer.writerow([side, index, *point])
    elif format == "dat":
        # A closed contour, not an IGG SECTIONAL file. Retain finite TE gap.
        points = list(reversed(record["suction"])) + record["pressure"][1:]
        if not np.allclose(points[0], points[-1], rtol=0, atol=1e-14):
            points.append(points[0])
        stream.write(record["geometry_id"] + "\n")
        for x, y in points:
            stream.write(f"{x:.16g} {y:.16g}\n")
    else:
        raise ValueError("坐标格式仅支持 csv / dat")
    return stream.getvalue()
