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
    "Wall": ("ada.api.walls", "Wall"),
    "Pipe": ("ada.api.piping", "Pipe"),
    "PipeSegStraight": ("ada.api.piping", "PipeSegStraight"),
    "PipeSegElbow": ("ada.api.piping", "PipeSegElbow"),
    "Boolean": ("ada.api.boolean", "Boolean"),
    # BoolHalfSpace lives in a submodule the primitives package __init__ does
    # not re-export, so the fallback scan misses it; map it explicitly. The IFC
    # writer (ada.cadit.ifc.write.geom.surfaces) does ``from ada import
    # BoolHalfSpace`` — without this, to_ifc fails under wasm with ImportError.
    "BoolHalfSpace": ("ada.api.primitives.bool_half_space", "BoolHalfSpace"),
    "Shape": ("ada.api.primitives", "Shape"),
    "PrimBox": ("ada.api.primitives", "PrimBox"),
    "PrimCyl": ("ada.api.primitives", "PrimCyl"),
    "PrimCone": ("ada.api.primitives", "PrimCone"),
    "PrimSphere": ("ada.api.primitives", "PrimSphere"),
    "PrimExtrude": ("ada.api.primitives", "PrimExtrude"),
    "PrimRevolve": ("ada.api.primitives", "PrimRevolve"),
    "PrimSweep": ("ada.api.primitives", "PrimSweep"),
    "Node": ("ada.api.nodes", "Node"),
    "Group": ("ada.api.groups", "Group"),
    "Instance": ("ada.api.transforms", "Instance"),
    "Placement": ("ada.api.transforms", "Placement"),
    "Transform": ("ada.api.transforms", "Transform"),
}


# Fallback scan: pyodide-safe modules that re-export top-level API names.
# After the explicit map, ``__getattr__`` scans these for the requested
# name so any pure-python export (Equipment, Surface, Connection, the FEM
# concept types, …) resolves without enumerating each by hand. Order
# matters only for disambiguation; _factories first so the from_* factories
# win. Every module here imports cleanly under pyodide (when its deps are
# present); a module that can't import in the current stack is skipped.
_LAZY_MODULES = (
    "ada._factories",
    "ada.api.spatial",
    "ada.api.spatial.equipment",
    "ada.api.beams",
    "ada.api.plates",
    "ada.api.primitives",
    "ada.api.piping",
    "ada.api.walls",
    "ada.api.curves",
    "ada.api.fasteners",
    "ada.api.groups",
    "ada.api.mass",
    "ada.api.nodes",
    "ada.api.transforms",
    "ada.api.boolean",
    "ada.api.connections",
    "ada.api.user",
    "ada.fem",
    "ada.fem.concept.constraints",
    "ada.fem.concept.loads",
    "ada.geom.direction",
    "ada.geom.points",
    "ada.base.units",
    "ada.core.utils",
    "ada.materials",
    "ada.sections",
)


def __getattr__(name: str):
    import importlib

    # Explicit map first (canonical source / disambiguation).
    target = _LAZY_EXPORTS.get(name)
    candidates = (target[0],) if target is not None else _LAZY_MODULES
    attr = target[1] if target is not None else name

    last_import_error = None
    for modname in candidates:
        try:
            module = importlib.import_module(modname)
        except ImportError as exc:
            # Module's deps aren't in this stack (e.g. the CAD-only stack
            # without pyquaternion) — skip; another module may still have it.
            last_import_error = exc
            continue
        if hasattr(module, attr):
            return getattr(module, attr)

    if last_import_error is not None:
        # Known-ish name, but nothing that defines it could import here.
        raise AttributeError(
            f"{name!r} is unavailable in this environment: {last_import_error}"
        ) from last_import_error
    raise AttributeError(
        f"module 'ada' has no attribute {name!r} — the pyodide/WASM build of adapy "
        f"exposes only a pure-python subset; CAD-kernel-backed names require the native build."
    )


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
