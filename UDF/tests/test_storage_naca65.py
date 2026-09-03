from __future__ import annotations

from cascade_forge.benchmark_naca65 import benchmark_specs, run_naca65_benchmark
from cascade_forge.models_naca65 import NACA65BenchmarkConfig
from cascade_forge.storage_naca65 import NACA65BenchmarkStore


def _small_config() -> NACA65BenchmarkConfig:
    return NACA65BenchmarkConfig(
        thickness_ratios=(0.10,),
        design_lift_coefficients=(0.8,),
        series_65_loading_parameters=(0.8,),
        max_outer_iterations=1,
        max_design_evaluations=10,
    )


def test_benchmark_matrix_has_fixed_36_cases() -> None:
    config = NACA65BenchmarkConfig()
    specs = benchmark_specs(config)
    assert config.case_count == len(specs) == 36
    assert sum(spec.family.value == "65" for spec in specs) == 27
    assert sum(spec.family.value == "65A" for spec in specs) == 9


def test_independent_benchmark_store_and_resume(tmp_path) -> None:
    root = tmp_path / "naca65-benchmark"
    first = run_naca65_benchmark(_small_config(), root)
    store = NACA65BenchmarkStore(root)
    assert first.total_cases == first.completed_cases == 2
    assert store.count() == 2
    rows = store.list_cases(limit=10)
    assert {row["family"] for row in rows} == {"65", "65A"}
    detail = store.get_case(rows[0]["fit_id"])
    assert detail is not None
    assert len(detail["reference"]["normalized_suction"]) == 201
    assert detail["fit_report"]["explicit_spec"]

    second = run_naca65_benchmark(_small_config(), root)
    assert second.run_id == first.run_id
    assert store.count() == 2
    assert second.files == first.files


def test_benchmark_job_state_is_persistent(tmp_path) -> None:
    store = NACA65BenchmarkStore(tmp_path / "benchmark")
    job_id = store.create_job("naca65_benchmark", {"cases": 36})
    store.update_job(job_id, status="completed", result={"completed_cases": 36})
    job = store.get_job(job_id)
    assert job is not None
    assert job["status"] == "completed"
    assert job["result"] == {"completed_cases": 36}
