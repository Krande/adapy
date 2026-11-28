"""Pyodide entrypoint: a procedural model's Excel workbook, relocation proposals and
CAD/analysis export, in the browser.

The browser-side twin of the server's ``procedural_export_xlsx``,
``procedural_import_xlsx``, ``procedural_relocations`` and ``procedural_export_model``
jobs (``ada.comms.rest.formats.procedural_export`` / ``procedural_detail``): the same
engine calls on a document the caller hands over, with no database and no storage.
The viewer's Pyodide worker calls :func:`run`; the node check scripts call the same
function, so what they test is what the browser runs.

* ``export_xlsx``: the engine's workbook (bytes), ``_ADA_META`` stamped.
* ``import_xlsx``: a workbook (bytes) parsed into a procedural document (JSON), the
  engine stamped on it. Creating the model is the caller's (a REST create + commit).
* ``propose_relocations``: the proposals document (JSON).
* ``export_model``: ``ifc`` (the detail model), ``gxml`` (the simulation model as a
  Genie concept XML) or ``gnx`` (that XML repacked as a Genie workspace).

The engine is the built-in default (``None`` / ``"adapy-default"``), another built-in
slug, or the slug of an engine that announced itself in this process
(``register_procedural_engine_capabilities``: a baked plugin wheel's register hook).

Catalog equipment comes from the caller (``equipment``: ``{slug: catalog doc}``), as
the server resolves it from its database; without it placed catalog equipment falls
back to the built-in archetypes. Linked CAD geometry is not spliced here: an IFC with
CAD equipment is the worker's.
"""

from __future__ import annotations

import json
import math
import os
import tempfile

__all__ = ["run", "export_xlsx", "import_xlsx", "propose_relocations", "export_model", "ACTIONS", "MODEL_FORMATS"]

ACTIONS = ("export_xlsx", "import_xlsx", "propose_relocations", "export_model")
MODEL_FORMATS = ("ifc", "gxml", "gnx")


def _engine(engine) -> str | None:
    # JS null crosses the Pyodide bridge as a JsNull proxy, not None (see wasm_compile).
    if not isinstance(engine, str) or not engine.strip():
        return None
    return engine.strip()


def _doc(doc) -> dict:
    return json.loads(doc) if isinstance(doc, str) else dict(doc)


def _registered_manifest(engine: str | None) -> dict | None:
    """The manifest-shaped doc of an engine registered in THIS process, as a worker
    reads it off its own registry (``procedural_support._advertised_engine_doc``):
    what locates a registered engine's ``export_xlsx``/``import_xlsx``. None for the
    built-in engines and for an engine nobody registered here."""
    from ada.topo_model.engines import BUILTIN_ENGINES, is_default_engine

    if is_default_engine(engine) or engine in BUILTIN_ENGINES:
        return None
    from ada.core.engine_specs import manifest_from_spec
    from ada.topo_model.engine_catalog import is_offerable, procedural_engine_specs

    for spec in procedural_engine_specs():
        if spec.get("slug") == engine and is_offerable(spec):
            return manifest_from_spec(spec)
    return None


def _plain(value):
    """``value`` as plain JSON: numpy scalars as Python numbers, NaN/inf as null (a
    workbook's empty cells read as NaN, which ``JSON.parse`` refuses)."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    item = getattr(value, "item", None)
    if callable(item) and not isinstance(value, (str, bytes)):
        try:
            value = item()
        except (TypeError, ValueError):
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def export_xlsx(doc, engine=None, name: str = "procedural-model") -> bytes:
    """The engine's Excel workbook for ``doc`` (bytes)."""
    from ada.topo_model.engines import export_doc_to_xlsx

    engine = _engine(engine)
    return export_doc_to_xlsx(engine, _doc(doc), name=name, manifest_doc=_registered_manifest(engine))


def import_xlsx(xlsx_bytes: bytes, engine=None) -> dict:
    """The procedural document an engine's workbook describes, with the engine stamped
    so a later compile routes back to it (as the server's import does)."""
    from ada.topo_model.engines import DEFAULT_ENGINE_SLUG, import_xlsx_to_doc

    engine = _engine(engine)
    if engine == DEFAULT_ENGINE_SLUG:
        engine = None
    parsed = import_xlsx_to_doc(engine, bytes(xlsx_bytes), manifest_doc=_registered_manifest(engine))
    parsed = dict(parsed)
    parsed["engine"] = engine or DEFAULT_ENGINE_SLUG
    return _plain(parsed)


def propose_relocations(doc, equipment: dict | None = None) -> dict:
    """The relocation proposals for ``doc`` (``{proposals, unresolved, baseline_problems}``)."""
    from ada.topo_model.relocate import propose_relocations as _propose

    catalog = dict(equipment or {})
    return _plain(_propose(_doc(doc), equipment_resolver=catalog.get))


def export_model(
    doc,
    fmt: str,
    name: str = "procedural-model",
    equipment: dict | None = None,
    detailing: str | None = None,
) -> bytes:
    """``doc`` as an ``ifc`` / ``gxml`` / ``gnx`` file (bytes), built by the built-in
    engine the way the server's export job builds it, minus CAD equipment."""
    from ada.topo_model.compile import build_procedural_assembly

    fmt = (fmt or "").strip().lower()
    if fmt not in MODEL_FORMATS:
        raise ValueError(f"unsupported export format {fmt!r} (expected one of {', '.join(MODEL_FORMATS)})")
    detailing = detailing if isinstance(detailing, str) and detailing.strip() else None
    catalog = dict(equipment or {})
    export_doc = {**_doc(doc), "equipment_cad": False}
    asm = build_procedural_assembly(
        export_doc,
        name=name,
        lod="detail" if fmt == "ifc" else "sim",
        detailing=detailing if fmt == "ifc" else None,
        equipment_resolver=catalog.get,
    )
    with tempfile.TemporaryDirectory() as d:
        if fmt == "ifc":
            p = os.path.join(d, "model.ifc")
            asm.to_ifc(p, file_obj_only=False)
        else:
            from ada.api.spatial.eq_types import EquipRepr
            from ada.api.spatial.equipment import Equipment

            # As the server's export: equipment defaults to AS_IS, which the Genie
            # writer skips; FOOTPRINT_MASS exports it as a Genie equipment concept.
            for part in asm.get_all_parts_in_assembly(include_self=True):
                if isinstance(part, Equipment) and part.eq_repr == EquipRepr.AS_IS:
                    part.eq_repr = EquipRepr.FOOTPRINT_MASS
            p = os.path.join(d, "model.gxml")
            asm.to_genie_xml(p, embed_sat=False)
            if fmt == "gnx":
                from ada.cadit.gxml.write.write_gnx import gnx_from_genie_xml

                p = str(gnx_from_genie_xml(p, os.path.join(d, "model.gnx")))
        with open(p, "rb") as fh:
            return fh.read()


def run(action: str, payload, data: bytes | None = None):
    """One call for the Pyodide worker: ``action`` (one of :data:`ACTIONS`), ``payload``
    (a JSON string or dict: ``doc``, ``engine``, ``name``, ``format``, ``equipment``,
    ``detailing``) and, for ``import_xlsx``, the workbook bytes.

    Returns bytes for ``export_xlsx`` / ``export_model`` and a JSON string for
    ``import_xlsx`` (the document) and ``propose_relocations`` (the proposals)."""
    opts = json.loads(payload) if isinstance(payload, str) else dict(payload or {})
    name = opts.get("name") or "procedural-model"
    if action == "export_xlsx":
        return export_xlsx(opts.get("doc") or {}, opts.get("engine"), name=name)
    if action == "import_xlsx":
        if data is None:
            raise ValueError("import_xlsx needs the workbook bytes")
        return json.dumps(import_xlsx(data, opts.get("engine")))
    if action == "propose_relocations":
        return json.dumps(propose_relocations(opts.get("doc") or {}, opts.get("equipment")))
    if action == "export_model":
        return export_model(
            opts.get("doc") or {},
            opts.get("format") or "",
            name=name,
            equipment=opts.get("equipment"),
            detailing=opts.get("detailing"),
        )
    raise ValueError(f"unknown procedural io action {action!r} (expected one of {', '.join(ACTIONS)})")
