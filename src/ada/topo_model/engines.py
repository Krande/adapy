"""Procedural-engine resolution + dispatch.

A procedural *engine* compiles a cell-model document to GLB bytes. The built-in
``adapy-default`` engine is :mod:`ada.topo_model.compile`; external engines are
separate Python packages (e.g. a topology library) that expose a
``module:callable`` entrypoint with the contract ``compile(doc) -> bytes``.

Both compile paths — the server ``procedural_build`` worker and the in-browser
Pyodide entrypoint (:mod:`ada.topo_model.wasm_compile`) — resolve and call an
engine THROUGH THIS MODULE, so engine selection behaves identically server-side
and in WASM.

An engine is selected by ``slug`` (a built-in like ``echo``, or a registered
one) or directly by its ``module:callable`` entrypoint string (as carried in a
registry manifest). The default engine keeps its own richer call path (it takes
catalog resolvers the generic contract doesn't), so this module only dispatches
the NON-default engines through the uniform ``compile(doc, **options)`` contract.
"""

from __future__ import annotations

import importlib
import inspect
from typing import Callable

__all__ = [
    "DEFAULT_ENGINE_SLUG",
    "BUILTIN_ENGINES",
    "is_default_engine",
    "entrypoint_for",
    "load_entrypoint",
    "compile_with_engine",
]

DEFAULT_ENGINE_SLUG = "adapy-default"

# Built-in engines shipped inside the adapy wheel — available both server-side
# and in the browser (they import only adapy, no external wheel). Each maps a
# slug to its ``module:callable`` entrypoint.
#
# ``echo`` is a diagnostic engine: it renders the document's cells as raw boxes
# (no structural blueprint), so selecting it produces a visibly-different model —
# proof that engine selection resolved and dispatched to a NON-default entrypoint
# on whichever path (server or WASM) ran the compile.
BUILTIN_ENGINES: dict[str, dict] = {
    DEFAULT_ENGINE_SLUG: {
        "name": "adapy default",
        "description": "Built-in adapy procedural engine (runs server-side and in-browser via WASM).",
        "entrypoint": "ada.topo_model.wasm_compile:compile_doc",
    },
    "echo": {
        "name": "echo (raw cells)",
        "description": "Diagnostic engine: renders the document's cells as raw boxes (no structure).",
        "entrypoint": "ada.topo_model.echo_engine:compile_doc",
    },
}


def is_default_engine(engine: str | None) -> bool:
    """True for the built-in default engine (``None`` / ``"adapy-default"``)."""
    return engine is None or engine == DEFAULT_ENGINE_SLUG


def entrypoint_for(engine: str) -> str:
    """The ``module:callable`` entrypoint for an engine selector — a built-in
    slug, or an explicit ``module:callable`` string passed straight through
    (as a registry manifest carries it). Raises on an unknown slug."""
    if ":" in engine:
        return engine
    spec = BUILTIN_ENGINES.get(engine)
    if spec is None:
        raise ValueError(f"unknown procedural engine {engine!r} (known built-ins: {sorted(BUILTIN_ENGINES)})")
    return spec["entrypoint"]


def load_entrypoint(entrypoint: str) -> Callable:
    """Import and return the callable named by a ``module:callable`` entrypoint."""
    module_name, sep, attr = entrypoint.partition(":")
    if not sep or not attr:
        raise ValueError(f"engine entrypoint must be 'module:callable', got {entrypoint!r}")
    fn = getattr(importlib.import_module(module_name), attr, None)
    if not callable(fn):
        raise ValueError(f"engine entrypoint {entrypoint!r} does not resolve to a callable")
    return fn


def _call_filtered(fn: Callable, doc, options: dict) -> bytes:
    """Call ``fn(doc, **options)`` passing only the keyword options the callable
    actually accepts — so a minimal external ``compile(doc)`` and a richer
    ``compile_doc(doc, name, lod, ...)`` both work under one call site."""
    try:
        params = inspect.signature(fn).parameters
        if any(p.kind == p.VAR_KEYWORD for p in params.values()):
            kwargs = dict(options)
        else:
            kwargs = {k: v for k, v in options.items() if k in params}
    except (TypeError, ValueError):
        kwargs = {}
    return fn(doc, **kwargs)


def compile_with_engine(engine: str, doc, **options) -> bytes:
    """Resolve ``engine`` (a non-default slug or a ``module:callable`` entrypoint)
    to its compile callable and invoke it as ``compile(doc, **options)``,
    returning the GLB bytes. ``options`` (e.g. ``name``, ``lod``) are filtered to
    those the engine accepts.

    The default engine is NOT routed here — callers keep its richer path (it
    takes the catalog/CAD resolvers this generic contract omits)."""
    if is_default_engine(engine):
        raise ValueError("compile_with_engine is for non-default engines; call the default compile path directly")
    return _call_filtered(load_entrypoint(entrypoint_for(engine)), doc, options)
