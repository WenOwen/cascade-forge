from typer.testing import CliRunner

from cascade_forge.cli import app


def test_only_three_top_level_commands():
    assert {item.name for item in app.registered_commands} == {"serve"}
    assert {item.name for item in app.registered_groups} == {"naca65", "arc"}


def test_removed_commands_are_rejected():
    runner = CliRunner()
    for name in (
        "generate",
        "export",
        "audit",
        "ingest-anchors",
        "fit-anchors",
        "build-blade",
        "workspace-list",
        "workspace-export",
        "workspace-import",
    ):
        assert runner.invoke(app, [name]).exit_code != 0


def test_serve_passes_root_layout_paths(tmp_path, monkeypatch):
    import uvicorn

    captured = {}
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda application, **kwargs: captured.update(application=application, **kwargs),
    )
    result = CliRunner().invoke(
        app,
        [
            "serve",
            "--dataset",
            str(tmp_path / "dataset"),
            "--databases",
            str(tmp_path / "databases"),
        ],
    )
    assert result.exit_code == 0, result.exception
    assert captured["application"].state.dataset_root == (tmp_path / "dataset").resolve()
