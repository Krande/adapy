"""Procedural cell models: key conventions + document validation.

The document is the single source of truth for a viewer-built cell model:
``{"grid": {...}, "spaces": [TopoSpace...], "equipments": [TopoEquipment...],
"openings": [TopoOpening...]}``. Validation round-trips through the
``ada.topology.entities`` pydantic models (shape/type checking only — geometry
completeness is the compile worker's job).
"""

from __future__ import annotations

from functools import lru_cache

PROCEDURAL_PREFIX = "_procedural/"
# Hidden prefix for built external-engine wheels (see HIDDEN_PREFIXES).
ENGINE_PREFIX = "_engines/"


def engine_wheel_dir(engine_id: str) -> str:
    """Blob-key prefix under which a ``kind:wheel`` engine's built wheel lives."""
    return f"{ENGINE_PREFIX}{engine_id}/"


def engine_wheel_key(engine_id: str, filename: str) -> str:
    """Blob key for a built engine wheel (``_engines/{id}/{filename}``). The
    filename is pip's ``name-version-py3-none-any.whl`` — micropip parses it, so
    it must be preserved verbatim."""
    return f"{engine_wheel_dir(engine_id)}{filename}"


def procedural_source_key(model_id: str, ext: str = ".xlsx") -> str:
    """Blob key for a procedural model's ORIGINAL source document (e.g. the
    param_models workbook it was imported from). Kept so a full-fidelity engine can
    compile the source directly (all config the topology doc drops); referenced by
    ``doc["source_xlsx_key"]``."""
    return f"{PROCEDURAL_PREFIX}{model_id}/source{ext.lower()}"


def _engine_suffix(engine: str | None) -> str:
    """Cache-key suffix distinguishing a non-default engine's output. The default
    engine keeps the bare key (backward compatible); any other engine's GLB is a
    separate blob so selecting it never serves the default's cached bytes."""
    if not engine or engine == "adapy-default":
        return ""
    # Keep the key filesystem/URL-safe (slugs are already, but be defensive).
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in engine)
    return f".{safe}"


def procedural_glb_key(model_id: str, revision: int, engine: str | None = None) -> str:
    """Blob key for a compiled model revision. Revision-stamped so the worker's
    cached-blob short-circuit makes recompiles of an unchanged revision free; the
    (non-default) ``engine`` gets its own key so engine outputs cache separately."""
    return f"{PROCEDURAL_PREFIX}{model_id}/r{revision}{_engine_suffix(engine)}.glb"


def procedural_detail_glb_key(model_id: str, revision: int, engine: str | None = None) -> str:
    """Blob key for the DETAIL level-of-detail compile of a model revision — a
    separate derived artifact from the simulation GLB (:func:`procedural_glb_key`),
    computed on demand when the user first opens the detail view. Revision-stamped
    the same way, so an unchanged revision's detail model is served from cache."""
    return f"{PROCEDURAL_PREFIX}{model_id}/r{revision}_detail{_engine_suffix(engine)}.glb"


def doc_content_hash(doc: dict) -> str:
    """A short, stable content hash of a (normalized) procedural doc — the cache
    key for an ephemeral *preview* compile. Deterministic (sorted keys, compact
    separators) so the same doc always hashes the same, and so a preview keyed on
    ``validate_doc(doc)`` matches the hash recomputed at commit time (letting the
    commit promote the already-built preview blob instead of recompiling)."""
    import hashlib
    import json

    payload = json.dumps(doc, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def procedural_preview_glb_key(
    model_id: str, doc_hash: str, engine: str | None = None, lod: str = "sim"
) -> str:
    """Blob key for an EPHEMERAL preview compile — a build of the current,
    *uncommitted* document so the user can visualize edits before deciding to
    commit. Keyed by the doc's content hash (not a revision), so re-previewing an
    unchanged doc is free and no revision is minted until the user commits. Lives
    under ``_procedural/{id}/preview/`` (hidden from listings, GC-able as a group)."""
    lod_suffix = "_detail" if lod == "detail" else ""
    return f"{PROCEDURAL_PREFIX}{model_id}/preview/{doc_hash}{lod_suffix}{_engine_suffix(engine)}.glb"


def procedural_log_key(glb_key: str) -> str:
    """Blob key for the engine-compile LOG captured alongside a procedural GLB.

    The log is a *sibling* of the GLB derived key — the same path with ``.log``
    swapped in for ``.glb`` — so a single rule covers every GLB variant (the
    committed ``r{rev}.glb``, its ``_detail`` LOD, an engine-suffixed key, and a
    ``preview/{hash}.glb``) and the log key can never drift from the key the
    worker actually wrote the GLB to. Deriving it from the GLB key (rather than
    re-deriving from model/revision/engine/lod) keeps a single source of truth."""
    if glb_key.endswith(".glb"):
        return f"{glb_key[: -len('.glb')]}.log"
    return f"{glb_key}.log"


def procedural_relocations_key(model_id: str) -> str:
    """Blob key for a model's latest relocation proposals (a JSON document). NOT
    revision-stamped: the proposal search always re-runs (the layout may have
    changed and it's cheap-ish), overwriting the previous result in place."""
    return f"{PROCEDURAL_PREFIX}{model_id}/relocations.json"


@lru_cache(maxsize=1)
def _doc_model():
    # Lazy: ada.topology.entities imports ada, which is heavy — only pay for it
    # on the first commit/compile, not at API boot.
    from typing import Optional

    from pydantic import BaseModel, Field

    from ada.topo_model.engines import DEFAULT_ENGINE_SLUG, PROCEDURAL_SCHEMA_VERSION

    # TopoSystem/SystemConnection are the importable, xlsx-ready twins of the
    # local closure classes this used to declare — reused here so the wire format
    # has a single source of truth (they carry ClassVars but dump the same shape).
    from ada.topology.entities import TopoEquipment, TopoLoftMember, TopoOpening, TopoSpace, TopoSystem

    class ProceduralDoc(BaseModel):
        # Routing/identity header (see ada.topo_model.engines.EngineBinding):
        # ``engine`` selects the procedural engine that compiles this model and
        # ``schema_version`` records the doc-schema it was authored against. Both
        # are mirrored onto the procedural_models row so a compile auto-routes.
        engine: str = DEFAULT_ENGINE_SLUG
        schema_version: str = PROCEDURAL_SCHEMA_VERSION
        grid: dict = Field(default_factory=dict)
        # blueprint compile options (whitelisted by the compiler), e.g.
        # {"reinforce_internal_walls": true}
        blueprint: dict = Field(default_factory=dict)
        # named design ruleset (routing/penetration rules) resolved by the
        # compiler via ada.topo_model.resolve_design_rules; unknown -> standard
        design_rules: Optional[str] = None
        # when true, catalog equipment with a linked CAD asset render as the
        # real CAD geometry (spliced in at compile) instead of a box
        equipment_cad: bool = False
        # blob key of the original source workbook this model was imported from
        # (see procedural_source_key). A full-fidelity engine compiles the source
        # directly (all config the topology sheets below drop); None for a
        # cellbuilder-authored model.
        source_xlsx_key: Optional[str] = None
        spaces: list[TopoSpace] = Field(default_factory=list)
        equipments: list[TopoEquipment] = Field(default_factory=list)
        openings: list[TopoOpening] = Field(default_factory=list)
        # routed service runs between equipment ports; the compiler wires,
        # routes and renders these (see ada.topo_model.compile)
        systems: list[TopoSystem] = Field(default_factory=list)
        # swept ("lofted") members: ordered section-profile stacks that compile
        # into inter-station band cells + plates (see ada.topo_model.builder)
        loft_members: list[TopoLoftMember] = Field(default_factory=list)

    return ProceduralDoc


def _validate_doc_shallow(doc: dict) -> dict:
    """Structural check for slim API deployments where ada (numpy) is not
    installed: list fields hold objects with a string NAME. Full pydantic
    validation then happens on the worker at compile time."""
    out = {
        "grid": doc.get("grid") or {},
        "blueprint": doc.get("blueprint") or {},
        "equipment_cad": bool(doc.get("equipment_cad")),
    }
    # Routing header — mirror EngineBinding's defaults here (ada isn't importable in
    # the slim image, so the constants can't be pulled from ada.topo_model.engines).
    engine = doc.get("engine")
    if engine is not None and not isinstance(engine, str):
        raise ValueError("engine must be a string")
    out["engine"] = engine or "adapy-default"
    schema_version = doc.get("schema_version")
    if schema_version is not None and not isinstance(schema_version, str):
        raise ValueError("schema_version must be a string")
    out["schema_version"] = schema_version or "1.0"
    source_xlsx_key = doc.get("source_xlsx_key")
    if source_xlsx_key is not None:
        if not isinstance(source_xlsx_key, str):
            raise ValueError("source_xlsx_key must be a string")
        out["source_xlsx_key"] = source_xlsx_key
    design_rules = doc.get("design_rules")
    if design_rules is not None:
        if not isinstance(design_rules, str):
            raise ValueError("design_rules must be a string")
        out["design_rules"] = design_rules
    if not isinstance(out["grid"], dict):
        raise ValueError("grid must be an object")
    if not isinstance(out["blueprint"], dict):
        raise ValueError("blueprint must be an object")
    for key in ("spaces", "equipments", "openings", "systems", "loft_members"):
        entries = doc.get(key) or []
        if not isinstance(entries, list):
            raise ValueError(f"{key} must be a list")
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict) or not isinstance(entry.get("NAME"), str) or not entry["NAME"]:
                raise ValueError(f"{key}[{i}] must be an object with a non-empty NAME")
        out[key] = entries
    return out


def validate_doc(doc: dict) -> dict:
    """Validate + normalize a procedural document by round-tripping it through
    the pydantic entity models. Raises ValueError with the pydantic error text
    on invalid input. Falls back to shallow structural validation when ada is
    not importable (slim API image)."""
    import pydantic

    if not isinstance(doc, dict):
        raise ValueError(f"doc must be an object, got {type(doc).__name__}")
    try:
        doc_model = _doc_model()
    except ImportError:
        return _validate_doc_shallow(doc)
    try:
        model = doc_model(**doc)
    except pydantic.ValidationError as e:
        raise ValueError(str(e)) from None
    return model.model_dump(mode="json")
