"""Human-readable labels derived without changing geometry identities."""

from typing import Any

from cascade_forge.domain.models import GeometrySpecV2


def geometry_label(spec: GeometrySpecV2) -> str:
    intent = spec.intent
    return (
        f"自定义 · βLE {intent.inlet_metal_angle_axial_deg:.1f}° · "
        f"βTE {intent.outlet_metal_angle_axial_deg:.1f}° · "
        f"γ {intent.stagger_deg:.1f}° · t/c {intent.max_thickness_ratio:.3f} · "
        f"xt/c {intent.max_thickness_x_ratio:.2f}"
    )


def record_label(record: dict[str, Any]) -> str:
    try:
        spec = record.get("spec", {})
        if record.get("family") == "custom":
            return geometry_label(GeometrySpecV2.model_validate(spec))
        if record.get("family") == "double_arc":
            mean = spec["mean_line"]
            return (
                f"双圆弧 · χ₁ {mean['inlet_metal_angle_deg']:.1f}° · "
                f"χ₂ {mean['outlet_metal_angle_deg']:.1f}° · "
                f"t/c {spec['max_thickness_ratio']:.3f}"
            )
        if record.get("family") == "naca65":
            return (
                f"{record.get('name', 'NACA65')} · "
                f"t/c {spec['thickness_ratio']:.3f} · "
                f"Clᵢ {spec['design_lift_coefficient']:.2f}"
            )
    except (ValueError, TypeError, KeyError):
        pass
    return str(record.get("name") or record.get("geometry_id") or "叶型")
