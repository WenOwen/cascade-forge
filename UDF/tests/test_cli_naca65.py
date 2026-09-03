from __future__ import annotations

import json

from typer.testing import CliRunner

from cascade_forge.benchmark_naca65 import run_naca65_benchmark
from cascade_forge.cli import app
from cascade_forge.models_naca65 import NACA65BenchmarkConfig
from cascade_forge.storage_naca65 import NACA65BenchmarkStore


def test_naca65_cli_generate_and_fit(tmp_path) -> None:
    runner = CliRunner()
    generated_path = tmp_path / "native.json"
    generated = runner.invoke(
        app,
        [
            "naca65",
            "generate",
            "--preset",
            "65-(12)10",
            "--destination",
            str(generated_path),
        ],
    )
    assert generated.exit_code == 0, generated.stdout
    payload = json.loads(generated_path.read_text(encoding="utf-8"))
    assert payload["spec"]["design_lift_coefficient"] == 1.2

    fit_path = tmp_path / "fit.json"
    fitted = runner.invoke(
        app,
        [
            "naca65",
            "fit",
            "--preset",
            "65-810",
            "--max-outer-iterations",
            "1",
            "--max-design-evaluations",
            "10",
            "--destination",
            str(fit_path),
        ],
    )
    assert fitted.exit_code == 0, fitted.stdout
    assert json.loads(fit_path.read_text(encoding="utf-8"))["fit_report"]["fit_id"]


def test_naca65_cli_export(tmp_path) -> None:
    dataset = tmp_path / "benchmark"
    config = NACA65BenchmarkConfig(
        thickness_ratios=(0.10,),
        design_lift_coefficients=(0.8,),
        series_65_loading_parameters=(0.8,),
        max_outer_iterations=1,
        max_design_evaluations=10,
    )
    run_naca65_benchmark(config, dataset)
    fit_id = NACA65BenchmarkStore(dataset).list_cases(limit=1)[0]["fit_id"]
    destination = tmp_path / "case.csv"
    result = CliRunner().invoke(
        app,
        [
            "naca65",
            "export",
            fit_id,
            "--dataset",
            str(dataset),
            "--destination",
            str(destination),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert destination.read_text(encoding="utf-8").startswith(
        "source,side,point,x_over_c,y_over_c"
    )
