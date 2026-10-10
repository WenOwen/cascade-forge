"""Additive native profile dataset; existing spline datasets remain untouched."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np

from cascade_forge.domain.circular_arc import generate_arc_profile
from cascade_forge.domain.geometry import generate_profile
from cascade_forge.domain.labels import record_label
from cascade_forge.domain.models import GeometrySpecV2
from cascade_forge.domain.models_circular_arc import ArcProfileSpec
from cascade_forge.domain.models_naca65 import NACA65Spec
from cascade_forge.domain.naca65 import generate_naca65


def generate_record(family: str, spec: dict[str, Any]) -> dict[str, Any]:
    if family == "custom":
        geometry = generate_profile(GeometrySpecV2.model_validate(spec))
        identifier = geometry.geometry_id
        suction, pressure, camber, _ = geometry.arrays()
        canonical = geometry.spec.model_dump(mode="json")
        name = "自定义叶型"
    elif family == "naca65":
        geometry = generate_naca65(NACA65Spec.model_validate(spec))
        identifier = geometry.native_id
        suction = np.asarray(geometry.normalized_suction)
        pressure = np.asarray(geometry.normalized_pressure)
        camber = np.asarray(geometry.camber)
        canonical = geometry.spec.model_dump(mode="json")
        name = geometry.designation
    elif family == "double_arc":
        parsed = ArcProfileSpec.model_validate(spec)
        if parsed.mean_line.family.value != "double":
            raise ValueError("标准叶型工作台仅支持双圆弧")
        geometry = generate_arc_profile(parsed)
        identifier = geometry.profile_id
        suction, pressure, camber, _ = geometry.arrays()
        canonical = geometry.spec.model_dump(mode="json")
        name = "双圆弧叶型"
    else:
        raise ValueError("不支持的叶型系列")
    record = {
        "schema_version": "native-profile-1.0",
        "geometry_id": identifier,
        "family": family,
        "name": name,
        "spec": canonical,
        "suction": suction.tolist(),
        "pressure": pressure.tolist(),
        "camber": camber.tolist(),
        "coordinate_convention": "chord-local:x-le-to-te,y-to-suction",
        "native_geometry": geometry.model_dump(mode="json"),
        "created_at": datetime.now(UTC).isoformat(),
    }

    record["label"] = record_label(record)
    return record
