"""The docs image's FEA report cache: reused only while every input is unchanged, never half-kept."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "docs" / "fea_report_cached.py"
if not _SCRIPT.is_file():
    pytest.skip("scripts/ is not shipped with the installed package's tests", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("fea_report_cached", _SCRIPT)
frc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frc)

CONFIG = '<script>(function(){var c=window.__PARADOC_CONFIG__=window.__PARADOC_CONFIG__||{};Object.assign(c,{"transport":"static"});})();</script>'


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    for rel, text in {
        "pixi.lock": "lock",
        "pixi.toml": "toml",
        "pyproject.toml": "py",
        "scripts/docs/fea_report_cached.py": "script",
        "src/ada/fem/solve.py": "x = 1",
        "src/frontend/embed.ts": "viewer",
        "verification/tasks.py": "t = 1",
        "verification/.cache/case.json": "{}",
        "verification/_assets/a.bin": "derived",
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


@pytest.fixture
def paradoc_dir(tmp_path):
    d = tmp_path / "paradoc"
    (d / "frontend/resources").mkdir(parents=True)
    (d / "__init__.py").write_text("v = 1")
    (d / frc._FRONTEND_ZIP).write_bytes(b"zip-1")
    return d


class Builder:
    """Stands in for the pixi tasks: writes the outputs, optionally failing an export."""

    def __init__(self, repo, fail=()):
        self.repo, self.fail, self.calls = repo, set(fail), []

    def __call__(self, task):
        self.calls.append(task)
        report = self.repo / frc.OUTPUTS[0]
        report.mkdir(parents=True, exist_ok=True)
        (report / "index.html").write_text(f"<html><head>{CONFIG}</head>old frontend</html>")
        (report / "manifest.json").write_text("results")
        return task not in self.fail


def _produce(repo, tmp_path, paradoc_dir, run, zip_path=None, keep=3):
    return frc.produce(repo, tmp_path / "cache", paradoc_dir, zip_path, keep, run)


def test_unchanged_inputs_reuse_the_report(repo, tmp_path, paradoc_dir):
    first = Builder(repo)
    assert _produce(repo, tmp_path, paradoc_dir, first) == "built"
    assert first.calls == ["fea-doc-build", "fea-doc-docx", "fea-doc-odt", "fea-doc-pdf"]
    frc.shutil.rmtree(repo / "docs")
    second = Builder(repo)
    assert _produce(repo, tmp_path, paradoc_dir, second) == "hit"
    assert second.calls == []
    assert (repo / frc.OUTPUTS[0] / "manifest.json").read_text() == "results"


@pytest.mark.parametrize(
    "rel", ["src/ada/fem/solve.py", "verification/tasks.py", "verification/.cache/case.json", "pixi.lock"]
)
def test_any_input_change_rebuilds(repo, tmp_path, paradoc_dir, rel):
    _produce(repo, tmp_path, paradoc_dir, Builder(repo))
    (repo / rel).write_text("changed")
    assert _produce(repo, tmp_path, paradoc_dir, Builder(repo)) == "built"


def test_paradoc_code_is_an_input_its_frontend_is_not(repo, tmp_path, paradoc_dir):
    before = frc.report_key(repo, paradoc_dir)
    (paradoc_dir / frc._FRONTEND_ZIP).write_bytes(b"zip-2")
    (paradoc_dir / "OVERLAY").write_text("paradoc abc1234 + adapy embed overlay")
    assert frc.report_key(repo, paradoc_dir) == before
    (paradoc_dir / "__init__.py").write_text("v = 2")
    assert frc.report_key(repo, paradoc_dir) != before


@pytest.mark.parametrize("rel", ["src/frontend/embed.ts", "verification/_assets/a.bin"])
def test_non_inputs_do_not_rebuild(repo, paradoc_dir, rel):
    before = frc.report_key(repo, paradoc_dir)
    (repo / rel).write_text("changed")
    assert frc.report_key(repo, paradoc_dir) == before


def test_a_failed_export_is_not_kept(repo, tmp_path, paradoc_dir):
    _produce(repo, tmp_path, paradoc_dir, Builder(repo, fail={"fea-doc-pdf"}))
    assert _produce(repo, tmp_path, paradoc_dir, Builder(repo)) == "built"


def test_a_failed_report_stops_the_build(repo, tmp_path, paradoc_dir):
    with pytest.raises(SystemExit, match="fea-doc-build"):
        _produce(repo, tmp_path, paradoc_dir, Builder(repo, fail={"fea-doc-build"}))


def test_a_reused_report_gets_the_current_frontend_and_keeps_its_config(repo, tmp_path, paradoc_dir):
    _produce(repo, tmp_path, paradoc_dir, Builder(repo))
    new = tmp_path / "frontend.zip"
    with zipfile.ZipFile(new, "w") as z:
        z.writestr("index.html", '<!doctype html><html><head lang="en"><title>t</title></head>new frontend</html>')
    assert _produce(repo, tmp_path, paradoc_dir, Builder(repo), zip_path=new) == "hit"
    html = (repo / frc.OUTPUTS[0] / "index.html").read_text()
    assert "new frontend" in html and "old frontend" not in html
    assert html.index(CONFIG) == html.index('<head lang="en">') + len('<head lang="en">')


def test_only_the_newest_reports_are_kept(repo, tmp_path, paradoc_dir):
    keys = []
    for i in range(3):
        (repo / "src/ada/fem/solve.py").write_text(f"x = {i}")
        _produce(repo, tmp_path, paradoc_dir, Builder(repo), keep=2)
        keys.append(frc.report_key(repo, paradoc_dir))
        cache = tmp_path / "cache"
        frc.os.utime(cache / keys[-1], (1000 + i, 1000 + i))
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == sorted(keys[1:])
