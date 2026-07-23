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


def procedural_glb_key(model_id: str, revision: int) -> str:
    """Blob key for a compiled model revision. Revision-stamped so the worker's
    cached-blob short-circuit makes recompiles of an unchanged revision free."""
    return f"{PROCEDURAL_PREFIX}{model_id}/r{revision}.glb"


@lru_cache(maxsize=1)
def _doc_model():
    # Lazy: ada.topology.entities imports ada, which is heavy — only pay for it
    # on the first commit/compile, not at API boot.
    from pydantic import BaseModel, Field

    from ada.topology.entities import TopoEquipment, TopoOpening, TopoSpace

    class ProceduralDoc(BaseModel):
        grid: dict = Field(default_factory=dict)
        spaces: list[TopoSpace] = Field(default_factory=list)
        equipments: list[TopoEquipment] = Field(default_factory=list)
        openings: list[TopoOpening] = Field(default_factory=list)

    return ProceduralDoc


def _validate_doc_shallow(doc: dict) -> dict:
    """Structural check for slim API deployments where ada (numpy) is not
    installed: list fields hold objects with a string NAME. Full pydantic
    validation then happens on the worker at compile time."""
    out = {"grid": doc.get("grid") or {}}
    if not isinstance(out["grid"], dict):
        raise ValueError("grid must be an object")
    for key in ("spaces", "equipments", "openings"):
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
