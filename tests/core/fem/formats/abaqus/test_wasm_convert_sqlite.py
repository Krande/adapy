"""The in-browser FEA bake of an already-exported Abaqus results ``.sqlite``.

``ada.cadit.wasm_convert.run("fea", "sqlite", ...)`` is exactly what the Pyodide
worker calls. It must bake with stdlib ``sqlite3`` alone, and must not register
the ``.odb`` reader: that one shells out to ``abaqus python``, which no browser
has — ``.odb`` stays a server-worker workload.
"""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import zipfile

import pytest

from ada.cadit import wasm_convert
from ada.fem.results.artefacts import _STREAM_READERS

FIXTURES = pathlib.Path(__file__).resolve().parents[5] / "files" / "fem_files" / "abaqus" / "results"


@pytest.mark.parametrize("name", ["beam", "shell"])
def test_sqlite_bakes_via_the_wasm_entrypoint(tmp_path, monkeypatch, name):
    src = tmp_path / f"{name}.sqlite"
    shutil.copy(FIXTURES / f"{name}.sqlite", src)
    monkeypatch.delitem(_STREAM_READERS, ".odb", raising=False)

    out = wasm_convert.run("fea", "sqlite", "glb", str(src))

    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        names = set(zf.namelist())
        assert "fea.manifest.json" in names
        manifest = json.loads(zf.read("fea.manifest.json"))
    assert manifest, "empty manifest"
    assert ".sqlite" in _STREAM_READERS
    assert ".odb" not in _STREAM_READERS, "the browser bake must not register the native .odb reader"


def test_the_browser_bake_records_the_source_key(tmp_path, monkeypatch):
    """The Pyodide worker stages the source as ``input.<ext>``; the manifest's ``src`` must be
    the source's storage key, as the worker's bake writes it, not that temporary stem (the
    case and envelope documents built in the browser copy it)."""
    src = tmp_path / "input.sqlite"
    shutil.copy(FIXTURES / "beam.sqlite", src)
    monkeypatch.delitem(_STREAM_READERS, ".odb", raising=False)

    out = wasm_convert.run("fea", "sqlite", "glb", str(src), src_key="results/run 1/beam.sqlite")
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        manifest = json.loads(zf.read("fea.manifest.json"))
    assert manifest["src"] == "results/run 1/beam.sqlite"

    # Without a key it stays the staged file's stem, as before.
    out = wasm_convert.run("fea", "sqlite", "glb", str(src))
    with zipfile.ZipFile(io.BytesIO(out)) as zf:
        assert json.loads(zf.read("fea.manifest.json"))["src"] == "input"
