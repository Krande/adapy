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

from pydantic import BaseModel

__all__ = [
    "DEFAULT_ENGINE_SLUG",
    "PROCEDURAL_SCHEMA_VERSION",
    "ENTITY_INCLUDE_SCHEMA",
    "honours_entity_include",
    "EngineBinding",
    "BUILTIN_ENGINES",
    "is_default_engine",
    "entrypoint_for",
    "load_entrypoint",
    "compile_with_engine",
    "engine_supports_excel",
    "export_doc_to_xlsx",
    "import_xlsx_to_doc",
]

DEFAULT_ENGINE_SLUG = "adapy-default"

# The procedural *document* schema version this adapy build authors and reads —
# bumped when the doc shape (sheets/entities/columns) changes incompatibly. It is
# independent of the xlsx serializer's own wire-format version. MAJOR.MINOR: a
# minor bump is additive (older reader still works); a major bump is a break.
#
# 1.1: an entity's ``INCLUDE: false`` (space, equipment, opening) leaves it out of the build. Before
# 1.1 the entity models' default False was dumped into every stored doc and no compile read it, so a
# 1.0 (or unstamped) doc's entity INCLUDE is ignored: see :func:`honours_entity_include`.
PROCEDURAL_SCHEMA_VERSION = "1.1"

#: The first doc schema whose entities' ``INCLUDE: false`` means "left out of the build".
ENTITY_INCLUDE_SCHEMA = "1.1"


def _version(v: object) -> tuple[int, ...] | None:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        return None


def honours_entity_include(schema_version: object) -> bool:
    """Does a doc of this schema mean its entities' ``INCLUDE: false``? Only from 1.1: an older or
    unstamped doc carries the models' default False on entities nobody excluded (``validate_doc`` and
    ``to_doc`` dumped it, and the compile ignored it), so reading it now would drop them. A structure's
    ``groups[].include: false`` is new with the flag and read at every version."""
    have, need = _version(schema_version), _version(ENTITY_INCLUDE_SCHEMA)
    return have is not None and need is not None and have >= need


class EngineBinding(BaseModel):
    """The engine + document-schema a procedural model is bound to.

    This is the routing/identity header of a procedural document: which engine
    compiles it (a built-in slug like ``adapy-default``/``echo``, or a registered
    engine's slug) and the doc-schema version it was authored against. It is
    carried by the workbook's ``Model`` sheet, stamped into the document
    (``doc["engine"]``/``doc["schema_version"]``), and persisted as first-class
    columns on the ``procedural_models`` row — so a compile auto-routes to the
    right engine (and its capability worker) without the caller naming it, and a
    schema drift between the file and this build is detectable.
    """

    engine: str = DEFAULT_ENGINE_SLUG
    schema_version: str = PROCEDURAL_SCHEMA_VERSION

    def is_compatible(self, current: str = PROCEDURAL_SCHEMA_VERSION) -> bool:
        """Same MAJOR version = compatible (minor bumps are additive)."""
        return self.schema_version.split(".", 1)[0] == current.split(".", 1)[0]


# Built-in engines shipped inside the adapy wheel — available both server-side
# and in the browser (they import only adapy, no external wheel). Each maps a
# slug to its ``module:callable`` entrypoint.
#
# ``echo`` is a diagnostic engine: it renders the document's cells as raw boxes
# (no structural blueprint), so selecting it produces a visibly-different model —
# proof that engine selection resolved and dispatched to a NON-default entrypoint
# on whichever path (server or WASM) ran the compile.
#
# ``supports_grouping`` advertises whether an engine understands the cellbuilder's
# per-group blueprints (a group == one structure with its own blueprint).
# adapy-default DOES: it maps a doc's ``groups``/``STRUCTURE_NAME`` to one
# per-structure topology model, each placed at its origin, via
# ``ada.topo_model.structures`` (see ``ProceduralBuilder.build_structure``); a
# doc with no groups still compiles the single model it always did. echo
# remains non-grouping — it renders raw boxes, no structure. A capability
# engine advertises ``True``/``False`` from its own worker specs (see
# ``ada.topo_model.engine_catalog`` / the worker heartbeat).
#
# ``supports_excel`` says whether an engine has an Excel format: adapy-default
# does, echo does not. A registered engine says the same through its
# registration (``register_procedural_engine_capabilities(supports_excel=...)``)
# or its manifest; one that says nothing has one.
BUILTIN_ENGINES: dict[str, dict] = {
    DEFAULT_ENGINE_SLUG: {
        "name": "adapy default",
        "description": "Built-in adapy procedural engine (runs server-side and in-browser via WASM).",
        "entrypoint": "ada.topo_model.wasm_compile:compile_doc",
        "supports_grouping": True,
        "supports_excel": True,
    },
    "echo": {
        "name": "echo (raw cells)",
        "description": "Diagnostic engine: renders the document's cells as raw boxes (no structure).",
        "entrypoint": "ada.topo_model.echo_engine:compile_doc",
        "supports_grouping": False,
        "supports_excel": False,
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


# ── Excel export / import ─────────────────────────────────────────────
#
# A procedural model can be exported to — and imported from — the OWNING engine's
# Excel workbook. The default engine (adapy-default) uses ada.topo_model.excel_io;
# a non-default engine exposes sibling ``export_xlsx`` / ``import_xlsx``
# entrypoints (a registry manifest may name them explicitly, else they are derived
# from the compile entrypoint's module). An engine whose spec or manifest says
# ``supports_excel: false`` (the built-in echo) has no Excel format.

# Built-in engine slugs that own an Excel format, read off BUILTIN_ENGINES (the
# default engine does; the diagnostic ``echo`` engine does not).
_EXCEL_BUILTIN_ENGINES: frozenset[str] = frozenset(
    slug for slug, spec in BUILTIN_ENGINES.items() if spec.get("supports_excel", True)
)


def _sibling_entrypoint(compile_entrypoint: str, func: str) -> str:
    """Derive an engine's ``export_xlsx``/``import_xlsx`` entrypoint from its
    ``module:compile`` entrypoint — the same module, a differently-named callable —
    so a manifest that names only ``entrypoint`` still yields the xlsx siblings."""
    module = compile_entrypoint.partition(":")[0]
    return f"{module}:{func}"


class EngineHasNoExcelFormat(ValueError):
    """Raised when export/import is attempted on an engine with no Excel format."""


def engine_supports_excel(engine: str | None, manifest_doc: dict | None = None) -> bool:
    """True if ``engine`` can export/import Excel: the default engine, or a
    registered engine whose manifest names an ``entrypoint`` (from which the xlsx
    siblings are derived) or explicit ``export_entrypoint``/``import_entrypoint``,
    and does not say ``supports_excel: false``. Built-in non-default engines
    (echo) return False."""
    if is_default_engine(engine):
        return True
    if engine in BUILTIN_ENGINES:
        return engine in _EXCEL_BUILTIN_ENGINES
    doc = manifest_doc or {}
    if doc.get("supports_excel") is False:
        return False
    return bool(doc.get("export_entrypoint") or doc.get("import_entrypoint") or doc.get("entrypoint"))


def _require_excel(engine: str | None, manifest_doc: dict | None) -> None:
    """Raise :class:`EngineHasNoExcelFormat` for an engine that says it has no Excel
    format: a built-in without one, or a manifest with ``supports_excel: false``."""
    no_builtin = engine in BUILTIN_ENGINES and engine not in _EXCEL_BUILTIN_ENGINES
    if no_builtin or (manifest_doc or {}).get("supports_excel") is False:
        raise EngineHasNoExcelFormat(f"engine {engine!r} has no Excel format")


def _xlsx_entrypoint(engine: str, manifest_doc: dict | None, *, direction: str) -> str:
    """Resolve the ``module:callable`` for an engine's ``export``/``import`` xlsx
    handler. Prefers the manifest's explicit ``{export,import}_entrypoint``; else
    derives it from the compile ``entrypoint``'s module. Raises when neither is
    available."""
    doc = manifest_doc or {}
    func = "export_xlsx" if direction == "export" else "import_xlsx"
    explicit = doc.get(f"{direction}_entrypoint")
    if explicit:
        return explicit
    compile_ep = doc.get("entrypoint") or (entrypoint_for(engine) if ":" in (engine or "") else None)
    if not compile_ep:
        raise EngineHasNoExcelFormat(f"engine {engine!r} manifest has no entrypoint to derive {func} from")
    return _sibling_entrypoint(compile_ep, func)


def export_doc_to_xlsx(engine: str | None, doc: dict, *, name: str, manifest_doc: dict | None = None) -> bytes:
    """Serialize a procedural ``doc`` to the ``engine``'s Excel workbook (bytes),
    with the ``_ADA_META`` sheet stamped. Raises :class:`EngineHasNoExcelFormat`
    for an engine with no Excel format."""
    if is_default_engine(engine):
        from ada.topo_model.excel_io import doc_to_xlsx_bytes

        return doc_to_xlsx_bytes(doc, name=name, engine=DEFAULT_ENGINE_SLUG)
    _require_excel(engine, manifest_doc)
    fn = load_entrypoint(_xlsx_entrypoint(engine, manifest_doc, direction="export"))
    return _call_filtered(fn, doc, {"name": name})


def import_xlsx_to_doc(engine: str | None, xlsx_bytes: bytes, *, manifest_doc: dict | None = None) -> dict:
    """Parse an ``engine``'s Excel workbook (bytes) into a procedural document.
    Raises :class:`EngineHasNoExcelFormat` for an engine with no Excel format.

    The engine's ``import_xlsx`` takes the workbook BYTES positionally (not a
    doc), so it is called directly rather than through the ``compile(doc, …)``
    filter."""
    if is_default_engine(engine):
        from ada.topo_model.excel_io import xlsx_bytes_to_doc

        return xlsx_bytes_to_doc(xlsx_bytes)
    _require_excel(engine, manifest_doc)
    fn = load_entrypoint(_xlsx_entrypoint(engine, manifest_doc, direction="import"))
    return fn(xlsx_bytes)
