"""Build a wheel from a source tree (the engine-build worker's core step)."""

from __future__ import annotations

import pathlib
import zipfile

from ada.comms.rest.engine_build import build_wheel_from_source

# A minimal pure-python package, built in a temp dir and turned into a wheel.
_PYPROJECT = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "echo-engine-fixture"
version = "0.1.0"
"""

_MODULE = "def compile(doc):\n    return b'glTF-stub'\n"


def _write_package(root: pathlib.Path) -> None:
    (root / "pyproject.toml").write_text(_PYPROJECT)
    pkg = root / "echo_engine_fixture"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(_MODULE)


def test_build_wheel_from_source(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_package(src)

    filename, data = build_wheel_from_source(src)

    # A pure-python package builds a universal wheel.
    assert filename.endswith("-py3-none-any.whl")
    assert filename.startswith("echo_engine_fixture-0.1.0")
    # It's a real, non-empty zip carrying the module.
    assert data[:2] == b"PK" and len(data) > 200
    import io

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    assert any(n.endswith("echo_engine_fixture/__init__.py") for n in names)
