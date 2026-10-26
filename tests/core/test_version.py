"""``ada.__version__`` resolution (#464: paradoc reports stamped adapy as v0.0.0)."""

import pathlib
from importlib.metadata import PackageNotFoundError

import pytest

from ada import _version

ROOT_PYPROJECT = pathlib.Path(_version.__file__).resolve().parents[2] / "pyproject.toml"


@pytest.fixture
def no_metadata(monkeypatch):
    """adapy off PYTHONPATH from a source copy: no installed distribution to ask."""

    def _missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(_version, "_pkg_version", _missing)
    monkeypatch.delenv("ADAPY_VERSION", raising=False)


def test_env_wins_over_installed_metadata(monkeypatch):
    monkeypatch.setattr(_version, "_pkg_version", lambda name: "1.2.3")
    monkeypatch.setenv("ADAPY_VERSION", " 9.9.9 ")
    assert _version._resolve_version() == "9.9.9"


def test_blank_env_is_ignored(monkeypatch):
    monkeypatch.setattr(_version, "_pkg_version", lambda name: "1.2.3")
    monkeypatch.setenv("ADAPY_VERSION", "  ")
    assert _version._resolve_version() == "1.2.3"


@pytest.mark.skipif(not ROOT_PYPROJECT.is_file(), reason="needs a source checkout")
def test_source_copy_reads_the_pyproject_beside_src(no_metadata):
    resolved = _version._resolve_version()
    assert resolved != "0.0.0"
    assert f'version = "{resolved}"' in ROOT_PYPROJECT.read_text(encoding="utf-8")


def test_a_foreign_pyproject_is_not_ours(no_metadata, monkeypatch, tmp_path):
    fake = tmp_path / "src" / "ada" / "_version.py"
    fake.parent.mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "other"\nversion = "4.5.6"\n')
    monkeypatch.setattr(_version, "__file__", str(fake))
    assert _version._resolve_version() == "0.0.0"
