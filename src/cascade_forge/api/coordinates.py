from __future__ import annotations

from typing import Any


def _naca65_coordinate_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    reference = case["reference"]
    fitted = case.get("fitted_geometry")
    sources = [
        ("native", "suction", reference["normalized_suction"]),
        ("native", "pressure", reference["normalized_pressure"]),
    ]
    if fitted:
        sources.extend(
            [
                ("bspline_fit", "suction", fitted["suction"]),
                ("bspline_fit", "pressure", fitted["pressure"]),
            ]
        )
    for source, side, coordinates in sources:
        for index, (x_value, y_value) in enumerate(coordinates):
            rows.append(
                {
                    "source": source,
                    "side": side,
                    "point": index,
                    "x_over_c": x_value,
                    "y_over_c": y_value,
                }
            )
    return rows
