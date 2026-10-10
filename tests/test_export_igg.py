import io
from zipfile import ZipFile

import numpy as np
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from cascade_forge.api.app import create_app
from cascade_forge.cli import app
from cascade_forge.domain.export_igg import naca65_igg_dat, naca65_igg_split_zip
from cascade_forge.domain.fitting_naca65 import fit_reference_geometry
from cascade_forge.domain.models_naca65 import NACA65BenchmarkConfig
from cascade_forge.domain.naca65 import generate_naca65
from cascade_forge.storage.naca65 import NACA65BenchmarkStore
from cascade_forge.workbench.benchmark_naca65 import run_naca65_benchmark


def read_curve_records(text):
    lines = iter(text.splitlines())
    curves = []
    for name in lines:
        assert name.isascii()
        assert next(lines) == "XYZ"
        count = int(next(lines))
        points = np.array([[float(v) for v in next(lines).split()] for _ in range(count)])
        assert points.shape == (count, 3)
        curves.append((name, points))
    return curves


def test_dat_keeps_complete_round_contour_and_separates_sources():
    reference = generate_naca65("65-810")
    _, fitted = fit_reference_geometry(reference)
    case = {"reference": reference.model_dump(), "fitted_geometry": fitted.model_dump()}
    for source, expected in [
        ("native", (reference.normalized_suction, reference.normalized_pressure)),
        ("fitted", (fitted.suction, fitted.pressure)),
    ]:
        curves = read_curve_records(naca65_igg_dat(case, source))
        assert len(curves) == 1
        name, xyz = curves[0]
        assert source in name
        assert len(xyz) == 401
        np.testing.assert_allclose(xyz[:201, :2], expected[0][::-1], atol=1e-16)
        np.testing.assert_allclose(xyz[201:, :2], expected[1][1:], atol=1e-16)
        assert (xyz[:, 2] == 0).all()
        np.testing.assert_array_equal(xyz[0], xyz[-1])
        assert (np.linalg.norm(np.diff(xyz, axis=0), axis=1) > 0).all()
    with pytest.raises(ValueError, match="没有拟合"):
        naca65_igg_dat({"reference": case["reference"]}, "fitted")


def test_dat_api_cli_and_unsaved_current_profile(tmp_path):
    root = tmp_path / "naca65"
    run_naca65_benchmark(
        NACA65BenchmarkConfig(
            compressor=True, thickness_ratios=(0.1,), design_lift_coefficients=(0.8,)
        ),
        root,
    )
    fit_id = NACA65BenchmarkStore(root).list_cases(limit=1)[0]["fit_id"]
    with TestClient(create_app(tmp_path / "v1", naca65_dataset_root=root)) as client:
        for source in ("native", "fitted"):
            response = client.get(
                f"/api/v2/naca65/cases/{fit_id}/export?format=dat&source={source}"
            )
            assert response.status_code == 200
            assert '.dat"' in response.headers["content-disposition"]
            path = tmp_path / f"{source}.dat"
            result = CliRunner().invoke(
                app,
                [
                    "naca65",
                    "export",
                    fit_id,
                    "--dataset",
                    str(root),
                    "--destination",
                    str(path),
                    "--source",
                    source,
                ],
            )
            assert result.exit_code == 0, result.output
            assert path.read_text(encoding="ascii") == response.text
            assert len(read_curve_records(response.text)) == 1
            split = client.get(
                f"/api/v2/naca65/cases/{fit_id}/export?format=dat&source={source}&layout=split"
            )
            assert split.status_code == 200
            assert split.headers["content-type"] == "application/zip"
            zip_path = tmp_path / f"{source}.zip"
            result = CliRunner().invoke(
                app,
                [
                    "naca65",
                    "export",
                    fit_id,
                    "--dataset",
                    str(root),
                    "--destination",
                    str(zip_path),
                    "--source",
                    source,
                    "--layout",
                    "split",
                ],
            )
            assert result.exit_code == 0, result.output
            assert zip_path.read_bytes() == split.content
        unsaved = generate_naca65("65-(12)10").model_dump(mode="json")
        response = client.post("/api/v2/naca65/export/dat", json={"reference": unsaved})
        assert response.status_code == 200
        split = client.post(
            "/api/v2/naca65/export/dat", json={"reference": unsaved, "layout": "split"}
        )
        assert split.status_code == 200
        assert split.content == naca65_igg_split_zip({"reference": unsaved})
        np.testing.assert_allclose(
            read_curve_records(response.text)[0][1][:201, :2], unsaved["normalized_suction"][::-1]
        )
        assert (
            client.post(
                "/api/v2/naca65/export/dat", json={"reference": unsaved, "source": "fitted"}
            ).status_code
            == 422
        )
        assert (
            client.get(f"/api/v2/naca65/cases/{fit_id}/export?format=dat&source=raw").status_code
            == 422
        )


@pytest.mark.parametrize("source", ["native", "fitted"])
def test_split_at_defined_le_preserves_every_side_point(source):
    reference = generate_naca65("65-810")
    _, fitted = fit_reference_geometry(reference)
    case = {"reference": reference.model_dump(), "fitted_geometry": fitted.model_dump()}
    with ZipFile(io.BytesIO(naca65_igg_split_zip(case, source))) as archive:
        assert sorted(archive.namelist()) == [
            f"65-810-{source}-pressure.dat",
            f"65-810-{source}-suction.dat",
        ]
        suction = read_curve_records(archive.read(f"65-810-{source}-suction.dat").decode())[0][1]
        pressure = read_curve_records(archive.read(f"65-810-{source}-pressure.dat").decode())[0][1]
    assert suction.shape == pressure.shape == (201, 3)
    np.testing.assert_array_equal(suction[-1], [0, 0, 0])
    np.testing.assert_array_equal(pressure[0], [0, 0, 0])
    np.testing.assert_array_equal(suction[0], pressure[-1])
    assert suction[:, 0].min() < -1e-4  # geometric leftmost point is NOT the split
    original = read_curve_records(naca65_igg_dat(case, source))[0][1]
    np.testing.assert_array_equal(np.vstack([suction, pressure[1:]]), original)
