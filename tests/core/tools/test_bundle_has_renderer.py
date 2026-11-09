"""A viewer bundle without the WebGPU renderer must not be committed: the option then always falls back to three.js."""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import zipfile

import pytest

_SCRIPT = pathlib.Path(__file__).parents[3] / "scripts" / "packaging" / "check_bundle_provenance.py"

pytestmark = pytest.mark.skipif(not _SCRIPT.is_file(), reason="no repo tree (sdist/wheel test env)")


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location("check_bundle_provenance", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _bundle(path: pathlib.Path, html: str) -> pathlib.Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("index.html", html)
    return path


def test_a_bundle_with_the_renderer_inlined_passes(check, tmp_path):
    html = '<script>window.ADACPP_RENDER_INLINE = {js: "AA", wasm: "BB"};</script>'
    assert check.has_renderer(_bundle(tmp_path / "index.zip", html))


def test_the_app_reading_the_global_does_not_count(check, tmp_path):
    html = "<script>const inline = globalThis.ADACPP_RENDER_INLINE;</script>"
    assert not check.has_renderer(_bundle(tmp_path / "index.zip", html))


def test_a_bundle_without_the_renderer_is_rejected(check, tmp_path, monkeypatch, capsys):
    bundle = _bundle(tmp_path / "index.zip", "<html></html>")
    monkeypatch.setattr(sys, "argv", ["check", "--bundle", str(bundle)])
    assert check.main() == 1
    assert "wasm-adacpp" in capsys.readouterr().err


def test_the_committed_bundle_has_the_renderer(check):
    assert check.has_renderer(check.BUNDLE)
