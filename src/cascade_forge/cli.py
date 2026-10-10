from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Annotated

import typer

from .audit import audit_dataset
from .audit_v2 import audit_dataset_v2
from .benchmark_naca65 import run_naca65_benchmark
from .blade3d import build_blade
from .circular_arc import (
    arc_feasibility_report,
    generate_arc_profile,
)
from .export_igg import naca65_igg_dat, naca65_igg_split_zip
from .fitting_naca65 import fit_reference_geometry
from .fitting_v2 import fit_anchors_to_dataset
from .geomturbo import export_geomturbo
from .legacy import ingest_legacy_anchors, legacy_section_schedule
from .models import SectionSchedule
from .models_circular_arc import (
    ArcFamily,
    ArcMeanLineSpec,
    ArcProfileSpec,
    MeanLineClosure,
)
from .models_naca65 import NACA65BenchmarkConfig, NACA65Family, NACA65Spec
from .naca65 import generate_naca65, parse_naca65_designation
from .sampling import generate_dataset
from .sampling_v2 import generate_dataset_v2
from .storage import DatasetStore
from .storage_naca65 import NACA65BenchmarkStore
from .storage_v2 import DatasetStoreV2
from .workspace import WorkspaceStore

app = typer.Typer(help="Cascade Forge 叶型几何数据库")
naca65_app = typer.Typer(help="NACA 65 压气机圆尾缘、65/65A 基础翼型生成与拟合")
arc_app = typer.Typer(help="圆弧族叶型：单圆弧 CA、双圆弧 DCA、多圆弧 MCA")
app.add_typer(naca65_app, name="naca65")
app.add_typer(arc_app, name="arc")
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = PACKAGE_ROOT / "configs" / "geometry-v1.0.0.yaml"
DEFAULT_CONFIG_V2 = PACKAGE_ROOT / "configs" / "geometry-v2.1.0.yaml"


@app.command("ingest-anchors")
def ingest_anchors_command(
    source: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    dataset: Annotated[Path, typer.Option()],
    raw_destination: Annotated[Path | None, typer.Option()] = None,
) -> None:
    manifest = ingest_legacy_anchors(source, dataset, raw_destination=raw_destination)
    typer.echo(
        json.dumps(
            {"count": manifest["count"], "dataset": str(dataset)},
            ensure_ascii=False,
        )
    )


@app.command("generate")
def generate_command(
    output: Annotated[Path, typer.Option()],
    config: Annotated[Path, typer.Option(exists=True)] = DEFAULT_CONFIG,
    target_valid: Annotated[int | None, typer.Option(min=1)] = None,
    dataset_version: Annotated[str, typer.Option()] = "1.0.0",
    workers: Annotated[int | None, typer.Option(min=1, max=64)] = None,
) -> None:
    def progress(state: dict[str, int]) -> None:
        typer.echo(
            f"proposals={state['proposals']} accepted={state['accepted']} "
            f"rejected={state['rejected']}"
        )

    selected_config = (
        DEFAULT_CONFIG_V2
        if dataset_version == "2.1.0" and config.resolve() == DEFAULT_CONFIG.resolve()
        else config
    )
    if dataset_version == "2.1.0" or "2.1.0" in selected_config.name:
        manifest = generate_dataset_v2(
            selected_config,
            output,
            target_valid=target_valid,
            workers=workers,
            progress=progress,
        )
    else:
        manifest = generate_dataset(
            selected_config, output, target_valid=target_valid, progress=progress
        )
    typer.echo(manifest.model_dump_json(indent=2))


@app.command("fit-anchors")
def fit_anchors_command(
    dataset: Annotated[Path, typer.Option()],
    source: Annotated[Path, typer.Option(exists=True, file_okay=False)] = (
        PACKAGE_ROOT / "data" / "raw" / "nasa120859"
    ),
) -> None:
    def progress(state: dict[str, int]) -> None:
        typer.echo(f"completed={state['completed']}/{state['total']}")

    result = fit_anchors_to_dataset(source, dataset, progress=progress)
    typer.echo(json.dumps(result, indent=2, ensure_ascii=False))


@app.command("audit")
def audit_command(dataset: Annotated[Path, typer.Option(exists=True, file_okay=False)]) -> None:
    summary = (
        audit_dataset_v2(dataset)
        if isinstance(_open_store(dataset), DatasetStoreV2)
        else audit_dataset(dataset)
    )
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["valid"]:
        raise typer.Exit(code=1)


@app.command("export")
def export_command(
    geometry_id: str,
    dataset: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    destination: Annotated[Path, typer.Option()],
) -> None:
    geometry = _open_store(dataset).get_geometry(geometry_id)
    if geometry is None:
        raise typer.BadParameter(f"找不到 {geometry_id}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    suction, pressure, _, _ = geometry.arrays()
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["side", "point", "x_over_c", "y_over_c"])
        for side, coordinates in (("suction", suction), ("pressure", pressure)):
            for index, coordinate in enumerate(coordinates):
                writer.writerow([side, index, *coordinate])
    typer.echo(str(destination.resolve()))


@app.command("build-blade")
def build_blade_command(
    dataset: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    destination: Annotated[Path, typer.Option()],
    schedule: Annotated[Path | None, typer.Option(exists=True)] = None,
    legacy_row: Annotated[str | None, typer.Option()] = None,
    raw_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = (
        PACKAGE_ROOT / "data" / "raw" / "nasa120859"
    ),
) -> None:
    if schedule is None and legacy_row is None:
        raise typer.BadParameter("必须提供 --schedule 或 --legacy-row")
    if schedule is not None:
        schedule_model = SectionSchedule.model_validate_json(schedule.read_text(encoding="utf-8"))
    else:
        schedule_model = legacy_section_schedule(
            raw_root, legacy_row or "", output_section_count=14
        )
    store = _open_store(dataset)
    blade = build_blade(schedule_model, store.get_geometry)
    path = export_geomturbo(blade, destination)
    typer.echo(str(path))


@app.command("serve")
def serve_command(
    dataset: Annotated[Path, typer.Option()],
    v2_dataset: Annotated[Path | None, typer.Option()] = None,
    workspace: Annotated[Path | None, typer.Option()] = None,
    naca65_dataset: Annotated[Path | None, typer.Option()] = None,
    databases: Annotated[Path | None, typer.Option()] = None,
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535)] = 8000,
) -> None:
    import uvicorn

    from .web import create_app

    uvicorn.run(
        create_app(
            dataset,
            v2_dataset_root=v2_dataset,
            workspace_root=workspace,
            naca65_dataset_root=naca65_dataset,
            profile_database_root=databases or Path(__file__).resolve().parents[3] / "databases",
        ),
        host=host,
        port=port,
    )


@app.command("workspace-list")
def workspace_list_command(
    workspace: Annotated[Path, typer.Option()],
) -> None:
    rows = WorkspaceStore(workspace).list_designs(limit=1000)
    typer.echo(json.dumps(rows, indent=2, ensure_ascii=False))


@app.command("workspace-export")
def workspace_export_command(
    design_id: str,
    workspace: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    destination: Annotated[Path, typer.Option()],
) -> None:
    path = WorkspaceStore(workspace).export_design(design_id, destination)
    typer.echo(str(path))


@app.command("workspace-import")
def workspace_import_command(
    source: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    workspace: Annotated[Path, typer.Option()],
) -> None:
    design_id = WorkspaceStore(workspace).import_design(source)
    typer.echo(design_id)


@naca65_app.command("generate")
def naca65_generate_command(
    family: Annotated[str, typer.Option()] = "65-compressor",
    thickness_ratio: Annotated[float, typer.Option(min=0.03, max=0.18)] = 0.10,
    design_lift_coefficient: Annotated[float, typer.Option(min=0.0, max=2.0)] = 0.8,
    loading_parameter_a: Annotated[float | None, typer.Option(min=0.0, max=1.0)] = None,
    preset: Annotated[str | None, typer.Option()] = None,
    destination: Annotated[Path | None, typer.Option()] = None,
) -> None:
    spec = _naca65_spec(
        family,
        thickness_ratio,
        design_lift_coefficient,
        loading_parameter_a,
        preset,
    )
    geometry = generate_naca65(spec)
    payload = geometry.model_dump_json(indent=2)
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
        typer.echo(str(destination.resolve()))
    else:
        typer.echo(payload)


@naca65_app.command("fit")
def naca65_fit_command(
    family: Annotated[str, typer.Option()] = "65-compressor",
    thickness_ratio: Annotated[float, typer.Option(min=0.03, max=0.18)] = 0.10,
    design_lift_coefficient: Annotated[float, typer.Option(min=0.0, max=2.0)] = 0.8,
    loading_parameter_a: Annotated[float | None, typer.Option(min=0.0, max=1.0)] = None,
    preset: Annotated[str | None, typer.Option()] = None,
    destination: Annotated[Path | None, typer.Option()] = None,
    max_outer_iterations: Annotated[int, typer.Option(min=1, max=20)] = 6,
    max_design_evaluations: Annotated[int, typer.Option(min=10, max=500)] = 80,
) -> None:
    reference = generate_naca65(
        _naca65_spec(
            family,
            thickness_ratio,
            design_lift_coefficient,
            loading_parameter_a,
            preset,
        )
    )
    report, fitted = fit_reference_geometry(
        reference,
        max_outer_iterations=max_outer_iterations,
        max_design_evaluations=max_design_evaluations,
    )
    payload = json.dumps(
        {
            "reference": reference.model_dump(mode="json"),
            "fit_report": report.model_dump(mode="json"),
            "fitted_geometry": fitted.model_dump(mode="json"),
        },
        indent=2,
        ensure_ascii=False,
    )
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
        typer.echo(str(destination.resolve()))
    else:
        typer.echo(payload)


@naca65_app.command("benchmark")
def naca65_benchmark_command(
    output: Annotated[Path, typer.Option()],
    compressor: Annotated[bool, typer.Option(help="运行独立压气机圆尾缘 9 型矩阵")] = False,
    max_outer_iterations: Annotated[int, typer.Option(min=1, max=20)] = 6,
    max_design_evaluations: Annotated[int, typer.Option(min=10, max=500)] = 80,
) -> None:
    config = NACA65BenchmarkConfig(
        compressor=compressor,
        max_outer_iterations=max_outer_iterations,
        max_design_evaluations=max_design_evaluations,
    )

    def progress(state: dict[str, int]) -> None:
        typer.echo(f"completed={state['completed']}/{state['total']} stored={state['stored']}")

    manifest = run_naca65_benchmark(config, output, progress=progress)
    typer.echo(manifest.model_dump_json(indent=2))


@naca65_app.command("export")
def naca65_export_command(
    fit_id: str,
    dataset: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    destination: Annotated[Path, typer.Option()],
    source: Annotated[str, typer.Option(help="DAT 轮廓来源：native 或 fitted")] = "native",
    layout: Annotated[str, typer.Option(help="closed：闭合 DAT；split：两侧 ZIP")] = "closed",
) -> None:
    case = NACA65BenchmarkStore(dataset).get_case(fit_id)
    if case is None:
        raise typer.BadParameter(f"找不到 {fit_id}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination.suffix.lower()
    if layout not in {"closed", "split"}:
        raise typer.BadParameter("layout 仅支持 closed 或 split")
    if layout == "split" and suffix != ".zip":
        raise typer.BadParameter("拆分导出请使用 --destination <文件名>.zip")
    if layout == "split":
        try:
            content = naca65_igg_split_zip(case, source)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        destination.write_bytes(content)
    elif suffix == ".dat":
        try:
            text = naca65_igg_dat(case, source)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        destination.write_text(text, encoding="ascii")
    elif suffix == ".json":
        destination.write_text(json.dumps(case, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        rows = _naca65_coordinate_rows(case)
        if suffix == ".parquet":
            import pyarrow as pa
            import pyarrow.parquet as pq

            pq.write_table(pa.Table.from_pylist(rows), destination, compression="zstd")
        elif suffix == ".csv":
            with destination.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        else:
            raise typer.BadParameter("destination 扩展名必须为 .dat、.csv、.json 或 .parquet")
    typer.echo(str(destination.resolve()))


@arc_app.command("generate")
def arc_generate_command(
    family: Annotated[str, typer.Option(help="single / double / multiple")] = "double",
    inlet_metal_angle_deg: Annotated[float, typer.Option(min=0.1, max=88.9)] = 30.0,
    outlet_metal_angle_deg: Annotated[float, typer.Option(min=0.1, max=88.9)] = 20.0,
    closure: Annotated[
        str, typer.Option(help="max_camber / junction_station / node_angles")
    ] = "max_camber",
    junction_station: Annotated[float | None, typer.Option(min=0.0, max=1.0)] = None,
    interior_node_angles: Annotated[
        str | None, typer.Option(help="多圆弧内节点切线角，逗号分隔（度）")
    ] = None,
    interior_node_stations: Annotated[
        str | None, typer.Option(help="多圆弧内节点弦向站位，逗号分隔")
    ] = None,
    max_thickness_ratio: Annotated[float, typer.Option(min=0.02, max=0.25)] = 0.10,
    max_thickness_station: Annotated[float, typer.Option(min=0.20, max=0.75)] = 0.40,
    leading_edge_radius_ratio: Annotated[float, typer.Option(min=0.0005, max=0.05)] = 0.006,
    trailing_edge_thickness_ratio: Annotated[float, typer.Option(min=0.0005, max=0.05)] = 0.006,
    trailing_edge_half_wedge_deg: Annotated[float, typer.Option(min=0.5, max=45.0)] = 6.0,
    points_per_side: Annotated[int, typer.Option(min=51, max=1001)] = 201,
    destination: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """生成圆弧族叶型（中弧线 + 法向厚度 + 完整轮廓）。"""

    spec = _arc_spec(
        family,
        inlet_metal_angle_deg,
        outlet_metal_angle_deg,
        closure,
        junction_station,
        interior_node_angles,
        interior_node_stations,
        max_thickness_ratio,
        max_thickness_station,
        leading_edge_radius_ratio,
        trailing_edge_thickness_ratio,
        trailing_edge_half_wedge_deg,
        points_per_side,
    )
    geometry = generate_arc_profile(spec)
    payload = geometry.model_dump_json(indent=2)
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload, encoding="utf-8")
        typer.echo(str(destination.resolve()))
    else:
        typer.echo(payload)


@arc_app.command("limits")
def arc_limits_command(
    family: Annotated[str, typer.Option(help="single / double / multiple")] = "double",
    max_thickness_ratio: Annotated[float, typer.Option(min=0.02, max=0.25)] = 0.10,
    max_thickness_station: Annotated[float, typer.Option(min=0.20, max=0.75)] = 0.40,
    leading_edge_radius_ratio: Annotated[float, typer.Option(min=0.0005, max=0.05)] = 0.006,
    trailing_edge_thickness_ratio: Annotated[float, typer.Option(min=0.0005, max=0.05)] = 0.006,
    trailing_edge_half_wedge_deg: Annotated[float, typer.Option(min=0.51, max=44.9)] = 6.0,
    inlet_metal_angle_deg: Annotated[float, typer.Option(min=0.1, max=88.9)] = 30.0,
    outlet_metal_angle_deg: Annotated[float, typer.Option(min=0.1, max=88.9)] = 20.0,
    interior_node_angles: Annotated[
        str | None, typer.Option(help="多圆弧内节点切线角，逗号分隔（度）")
    ] = None,
) -> None:
    """报告可行域：尾缘楔角上界、切点可达站位区间、多圆弧内节点站位可达区间。

    三类边界互不相同、不能互相替代。楔角上界取决于中弧线几何（总弧长 L、出口金属角
    χ₂、尾缘曲率 κ）**与**前缘半径标称量，因此这里构造完整的 spec 再求值：
    ``leading_edge_radius_ratio`` 直接影响前段单调判据，省略它（旧版的行为）会报出一个
    生成器实现不了的数。本命令的选项与 ``POST /api/v2/arc/limits`` 的请求体一一对应，
    两条路径的返回值必须逐位一致。

    不适用的字段返回 ``null``；该合同连最小允许楔角（0.51°）都实现不了时
    ``maximum_feasible_half_wedge_deg`` 也是 ``null``，同时 ``wedge_bound_exists`` 为
    ``false``——不报一个落在参数域之外的假上界。
    """

    spec = _arc_limits_spec(
        max_thickness_ratio,
        max_thickness_station,
        trailing_edge_thickness_ratio,
        inlet_metal_angle_deg,
        outlet_metal_angle_deg,
        family,
        interior_node_angles,
        leading_edge_radius_ratio,
        trailing_edge_half_wedge_deg,
    )
    try:
        report = arc_feasibility_report(spec)
    except (ValueError, ArithmeticError) as exc:
        raise typer.BadParameter(f"无法计算可行域: {exc}") from exc
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False))


def _arc_limits_spec(
    max_thickness_ratio: float,
    max_thickness_station: float,
    trailing_edge_thickness_ratio: float,
    inlet_metal_angle_deg: float,
    outlet_metal_angle_deg: float,
    family: str = "double",
    interior_node_angles: str | None = None,
    leading_edge_radius_ratio: float = 0.006,
    trailing_edge_half_wedge_deg: float = 6.0,
) -> ArcProfileSpec:
    """为 ``arc limits`` 组装 spec；选项集合与 ``/api/v2/arc/limits`` 的请求体一一对应。"""

    try:
        selected_family = ArcFamily(family.lower())
    except ValueError as exc:
        raise typer.BadParameter("family 支持 single、double 或 multiple") from exc
    try:
        return ArcProfileSpec(
            mean_line=ArcMeanLineSpec(
                family=selected_family,
                inlet_metal_angle_deg=inlet_metal_angle_deg,
                outlet_metal_angle_deg=outlet_metal_angle_deg,
                closure=MeanLineClosure.MAX_CAMBER,
                interior_node_angles_deg=(
                    _parse_float_list(interior_node_angles)
                    if selected_family == ArcFamily.MULTIPLE
                    else ()
                ),
            ),
            max_thickness_ratio=max_thickness_ratio,
            max_thickness_station=max_thickness_station,
            leading_edge_radius_ratio=leading_edge_radius_ratio,
            trailing_edge_thickness_ratio=trailing_edge_thickness_ratio,
            trailing_edge_half_wedge_deg=trailing_edge_half_wedge_deg,
        )
    except ValueError as exc:
        raise typer.BadParameter(f"无法构造中弧线以计算可行域: {exc}") from exc


def _parse_float_list(raw: str | None) -> tuple[float, ...]:
    if raw is None or not raw.strip():
        return ()
    try:
        return tuple(float(item) for item in raw.replace("，", ",").split(",") if item.strip())
    except ValueError as exc:
        raise typer.BadParameter(f"无法解析数值列表: {raw}") from exc


def _arc_spec(
    family: str,
    inlet_metal_angle_deg: float,
    outlet_metal_angle_deg: float,
    closure: str,
    junction_station: float | None,
    interior_node_angles: str | None,
    interior_node_stations: str | None,
    max_thickness_ratio: float,
    max_thickness_station: float,
    leading_edge_radius_ratio: float,
    trailing_edge_thickness_ratio: float,
    trailing_edge_half_wedge_deg: float,
    points_per_side: int,
) -> ArcProfileSpec:
    try:
        selected_family = ArcFamily(family.lower())
    except ValueError as exc:
        raise typer.BadParameter("family 支持 single、double 或 multiple") from exc
    try:
        selected_closure = MeanLineClosure(closure)
    except ValueError as exc:
        raise typer.BadParameter(
            "closure 支持 max_camber、junction_station 或 node_angles"
        ) from exc
    angles = _parse_float_list(interior_node_angles)
    stations = _parse_float_list(interior_node_stations)
    # 多圆弧省略站位时由 ``ArcMeanLineSpec`` 在验证前自动求解（与 API / Web 同源），
    # 这里不再复制一份 ``build_mca_mean_line`` 回退逻辑。
    mean_line = ArcMeanLineSpec(
        family=selected_family,
        inlet_metal_angle_deg=inlet_metal_angle_deg,
        outlet_metal_angle_deg=outlet_metal_angle_deg,
        closure=selected_closure,
        junction_station=(
            junction_station if selected_closure == MeanLineClosure.JUNCTION_STATION else None
        ),
        interior_node_angles_deg=angles,
        interior_node_stations=stations,
    )
    try:
        return ArcProfileSpec(
            mean_line=mean_line,
            max_thickness_ratio=max_thickness_ratio,
            max_thickness_station=max_thickness_station,
            leading_edge_radius_ratio=leading_edge_radius_ratio,
            trailing_edge_thickness_ratio=trailing_edge_thickness_ratio,
            trailing_edge_half_wedge_deg=trailing_edge_half_wedge_deg,
            points_per_side=points_per_side,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _naca65_spec(
    family: str,
    thickness_ratio: float,
    design_lift_coefficient: float,
    loading_parameter_a: float | None,
    preset: str | None,
) -> NACA65Spec:
    if preset:
        return parse_naca65_designation(preset)
    try:
        selected_family = NACA65Family(
            "65-compressor" if family.lower() == "65-compressor" else family.upper()
        )
    except ValueError as exc:
        raise typer.BadParameter("family 支持 65-compressor、65 或 65A") from exc
    if loading_parameter_a is None:
        loading_parameter_a = 1.0 if selected_family == NACA65Family.COMPRESSOR_65 else 0.8
    return NACA65Spec(
        family=selected_family,
        thickness_ratio=thickness_ratio,
        design_lift_coefficient=design_lift_coefficient,
        loading_parameter_a=loading_parameter_a,
    )


def _naca65_coordinate_rows(case: dict) -> list[dict]:
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
    return [
        {
            "source": source,
            "side": side,
            "point": index,
            "x_over_c": point[0],
            "y_over_c": point[1],
        }
        for source, side, coordinates in sources
        for index, point in enumerate(coordinates)
    ]


def _open_store(dataset: Path):
    if dataset.name.startswith("geometry-v2."):
        return DatasetStoreV2(dataset)
    manifest = dataset / "dataset_manifest.json"
    if manifest.exists():
        schema = json.loads(manifest.read_text(encoding="utf-8")).get("schema_version")
        if str(schema) == "2.1.0":
            return DatasetStoreV2(dataset)
    if (dataset / "tables" / "geometries").exists() and any(
        (dataset / "tables" / "geometries").glob("*.parquet")
    ):
        import pyarrow.parquet as pq

        first = next((dataset / "tables" / "geometries").glob("*.parquet"))
        if "intent_id" in pq.read_schema(first).names:
            return DatasetStoreV2(dataset)
    return DatasetStore(dataset)


if __name__ == "__main__":
    app()
