# Pyodide / wasm entrypoint for the `ada` package.
#
# adapy's full surface depends on native-only deps (pythonocc-core,
# python-gmsh, ifcopenshell-cpp) that pyodide cannot install. The real
# src/ada/__init__.py imports all of that eagerly, so it can't run under
# emscripten. Build steps that stage adapy for pyodide copy THIS file in
# over ada/__init__.py instead (see deploy/Dockerfile.viewer and
# tools/pyodide-test/test_pyodide_cad.js).
#
# Only the lazy `ada.cad` subpackage is supported here; it pulls its own
# adacpp.cad kernel on demand. `import ada.cad` works; any other
# `from ada import X` raises AttributeError, which is the right semantic
# — the feature is genuinely unavailable in this environment.
from __future__ import annotations

__author__ = "Kristoffer H. Andersen"

# ``cad`` is a lazily-imported subpackage (``import ada.cad``), not a
# module-level attribute, so ruff can't see it — advertise it anyway.
__all__ = ["cad"]  # noqa: F822


def _jupyter_nbextension_paths():
    return [
        {
            "section": "notebook",
            "src": "ada/_static",  # relative to this package
            "dest": "adapy",  # becomes /nbextensions/adapy/
            "require": "adapy/main",  # if your main bundle is main.js
        }
    ]


def _jupyter_labextension_paths():
    return [{"src": "ada/_static", "dest": "adapy"}]  # relative to this package  # labextensions/adapy
