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


# A curated set of *pure-python* top-level re-exports that the WASM-viable
# subpackages (ada.fem.results, ada.cadit.sat, ada.occ.tessellating, ...)
# pull via ``from ada import <Name>``. The real ada/__init__.py re-exports
# the whole API eagerly — which drags in pythonocc/gmsh/ifcopenshell and
# can't load under emscripten. Here we resolve only the kernel-free names,
# lazily, via PEP 562 ``__getattr__``; CAD-kernel-backed names (Beam,
# Plate, Assembly, ...) deliberately stay absent so a ``from ada import X``
# for them raises a clear AttributeError instead of importing the world.
#
# Keep entries pointed at their canonical source module (matching the real
# __init__'s import lines). Discovered/extended via
# tools/pyodide-test/test_pyodide_fem.js + test_pyodide_full.js.
#
# These names *import* fine under wasm (the api/geom/fem classes are
# pure-python); only their CAD operations (solid_geom → OCC tessellation)
# need a kernel, which fails at call time with a clear error if no backend
# is available. ``__getattr__`` maps ImportError → AttributeError so a name
# whose module needs an absent dep (e.g. the lightweight CAD-only stack
# without pyquaternion) simply reads as "not present" rather than blowing up.
_LAZY_EXPORTS = {
    # kernel-free top-level factories (impl in ada/_factories.py)
    "from_acis": ("ada._factories", "from_acis"),
    "from_fem": ("ada._factories", "from_fem"),
    "from_fem_res": ("ada._factories", "from_fem_res"),
    # geom datatypes
    "Direction": ("ada.geom.direction", "Direction"),
    "Point": ("ada.geom.points", "Point"),
    # curve api types (curve_utils does ``from ada import ArcSegment``)
    "ArcSegment": ("ada.api.curves", "ArcSegment"),
    "LineSegment": ("ada.api.curves", "LineSegment"),
    "CurvePoly2d": ("ada.api.curves", "CurvePoly2d"),
    "CurveRevolve": ("ada.api.curves", "CurveRevolve"),
    # fem
    "FEM": ("ada.fem", "FEM"),
    # base / core / units
    "Units": ("ada.base.units", "Units"),
    "Counter": ("ada.core.utils", "Counter"),
    # materials / sections
    "Material": ("ada.materials", "Material"),
    "Section": ("ada.sections", "Section"),
    # spatial + physical objects (pure-python to import; CAD ops are lazy)
    "Assembly": ("ada.api.spatial", "Assembly"),
    "Part": ("ada.api.spatial", "Part"),
    "Beam": ("ada.api.beams", "Beam"),
    "BeamTapered": ("ada.api.beams", "BeamTapered"),
    "BeamSweep": ("ada.api.beams", "BeamSweep"),
    "BeamRevolve": ("ada.api.beams", "BeamRevolve"),
    "Plate": ("ada.api.plates", "Plate"),
    "PlateCurved": ("ada.api.plates", "PlateCurved"),
    "Shape": ("ada.api.primitives", "Shape"),
    "Node": ("ada.api.nodes", "Node"),
    "Group": ("ada.api.groups", "Group"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(
            f"module 'ada' has no attribute {name!r} — the pyodide/WASM build of "
            f"adapy exposes only a pure-python subset; CAD-kernel-backed names "
            f"require the native build."
        )
    import importlib

    try:
        module = importlib.import_module(target[0])
        return getattr(module, target[1])
    except ImportError as exc:
        # The name is known but its module can't load in this stack (a
        # dependency isn't installed) — surface as AttributeError so
        # ``hasattr(ada, name)`` is a clean False rather than a hard error.
        raise AttributeError(f"{name!r} is unavailable in this environment: {exc}") from exc


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
