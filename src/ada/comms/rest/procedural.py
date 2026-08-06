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

    # TopoSystem/SystemConnection are the importable, xlsx-ready twins of the
    # local closure classes this used to declare — reused here so the wire format
    # has a single source of truth (they carry ClassVars but dump the same shape).
    from ada.topology.entities import TopoEquipment, TopoOpening, TopoSpace, TopoSystem

    class ProceduralDoc(BaseModel):
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
        spaces: list[TopoSpace] = Field(default_factory=list)
        equipments: list[TopoEquipment] = Field(default_factory=list)
        openings: list[TopoOpening] = Field(default_factory=list)
        # routed service runs between equipment ports; the compiler wires,
        # routes and renders these (see ada.topo_model.compile)
        systems: list[TopoSystem] = Field(default_factory=list)

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
    design_rules = doc.get("design_rules")
    if design_rules is not None:
        if not isinstance(design_rules, str):
            raise ValueError("design_rules must be a string")
        out["design_rules"] = design_rules
    if not isinstance(out["grid"], dict):
        raise ValueError("grid must be an object")
    if not isinstance(out["blueprint"], dict):
        raise ValueError("blueprint must be an object")
    for key in ("spaces", "equipments", "openings", "systems"):
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
