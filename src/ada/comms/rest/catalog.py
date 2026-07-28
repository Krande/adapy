"""Per-scope equipment-type and system-template catalogs: key conventions +
document validation.

Both catalogs store the reusable, placement-independent definition as a JSONB
``doc`` on a postgres row (see migrations 023/024). The equipment doc carries a
bounding box, mass, IFC element class and a port/nozzle list; a linked CAD asset
(under the hidden ``_equipment/`` prefix) lets the bbox + a preview GLB be
inferred by the ``equipment_bbox`` worker job. The system doc carries the
service category/type/medium/voltage and the routed-segment rendering knobs.

Validation is pure-pydantic (no ``ada`` import) so it runs in the slim API
image; the port geometry semantics mirror ``ada.api.systems.ports.Port``.
"""

from __future__ import annotations

import re
from functools import lru_cache

# Uploaded/copied CAD assets and inferred preview GLBs for equipment types live
# under this prefix, hidden from the scope file listing (see converter.py).
EQUIPMENT_PREFIX = "_equipment/"

_PORT_DIRECTIONS = ("IN", "OUT", "INOUT")
_PORT_CATEGORIES = ("process", "electrical", "signal")
_SYSTEM_TYPES = ("piping", "duct", "cable", "electrical")

# The built-in system kinds are static (mirrors ada.api.systems SYSTEM_KINDS), so
# the API can offer them + build a default template doc for "sync" WITHOUT a live
# worker or importing ada — the system catalog is never empty for want of a worker.
_SYSTEM_KIND_DEFAULTS = {
    "piping": {"category": "process", "voltage": None},
    "duct": {"category": "process", "voltage": None},
    "cable": {"category": "signal", "voltage": None},
    "electrical": {"category": "electrical", "voltage": 400},
}


# Built-in design rulesets (mirrors ada.topo_model.DESIGN_RULESETS) — static so
# the API can offer them in the dropdown WITHOUT a live worker or importing ada.
# Live workers may advertise more via ``procedural_design_rulesets``.
_DESIGN_RULESETS = (
    {
        "slug": "standard",
        "name": "Standard details",
        "description": "Route runs and add a penetration detail at each wall crossing "
        "(pipe sleeve / cable transit block / duct frame) with a through-hole cut in the wall plate.",
    },
    {
        "slug": "route_only",
        "name": "Route only",
        "description": "Route runs and detect wall crossings, but emit no penetration detail geometry.",
    },
)

#: Fallback ruleset slug when a document names none.
DEFAULT_DESIGN_RULESET = "standard"


def builtin_design_rulesets() -> list[dict]:
    """Specs for the built-in design rulesets (origin ``code``)."""
    return [dict(d) for d in _DESIGN_RULESETS]


def builtin_system_specs() -> list[dict]:
    """Catalog-shaped specs for the built-in system kinds (origin ``code``)."""
    specs = []
    for slug in _SYSTEM_TYPES:
        d = _SYSTEM_KIND_DEFAULTS[slug]
        specs.append(
            {
                "slug": slug,
                "name": slug.title(),
                "category": d["category"],
                "doc": {"type": slug, "medium": None, "voltage": d["voltage"], "pipe_radius": 0.05, "pipe_wt": 0.005},
            }
        )
    return specs


def equipment_cad_key(type_id: str, ext: str) -> str:
    """Blob key for an equipment type's source CAD asset. ``ext`` includes the
    leading dot (e.g. ``.step``); a single source per type (overwritten on
    re-upload)."""
    ext = ext if ext.startswith(".") else f".{ext}"
    return f"{EQUIPMENT_PREFIX}{type_id}/source{ext.lower()}"


def equipment_preview_glb_key(type_id: str) -> str:
    """Blob key for the equipment type's inferred preview GLB (sidecar viewer)."""
    return f"{EQUIPMENT_PREFIX}{type_id}/preview.glb"


def slugify(value: str) -> str:
    """A URL/identifier-safe slug: lowercase, non-alphanumerics collapsed to a
    single hyphen, trimmed. Empty input yields ``""`` (caller rejects)."""
    s = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return s


@lru_cache(maxsize=1)
def _equipment_doc_model():
    from typing import List, Literal, Optional

    from pydantic import BaseModel, Field, conlist

    Vec3 = conlist(float, min_length=3, max_length=3)

    class CatalogPort(BaseModel):
        name: str
        position: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
        direction_vector: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 1.0])
        direction: Literal["IN", "OUT", "INOUT"] = "INOUT"
        category: Literal["process", "electrical", "signal"] = "process"

    class BBox(BaseModel):
        lx: float = 1.0
        ly: float = 1.0
        lz: float = 1.0

    class EquipmentTypeDoc(BaseModel):
        bbox: BBox = Field(default_factory=BBox)
        mass: float = 1000.0
        # equipment-local centre of gravity; defaults to the bbox centroid at
        # compile time when omitted
        cog: Optional[Vec3] = None
        ifc_element_class: str = "IfcBuildingElementProxy"
        ports: List[CatalogPort] = Field(default_factory=list)

    return EquipmentTypeDoc


@lru_cache(maxsize=1)
def _system_doc_model():
    from typing import Literal, Optional

    from pydantic import BaseModel

    class SystemTemplateDoc(BaseModel):
        type: Literal["piping", "duct", "cable", "electrical"] = "piping"
        medium: Optional[str] = None
        # electrical service voltage in volts (informational; only meaningful
        # for electrical systems)
        voltage: Optional[int] = None
        pipe_radius: float = 0.05
        pipe_wt: float = 0.005

    return SystemTemplateDoc


def validate_equipment_doc(doc: dict) -> dict:
    """Validate + normalize an equipment-type document. Raises ValueError with
    the pydantic error text on invalid input; enforces unique port names."""
    import pydantic

    if not isinstance(doc, dict):
        raise ValueError(f"doc must be an object, got {type(doc).__name__}")
    try:
        model = _equipment_doc_model()(**doc)
    except pydantic.ValidationError as e:
        raise ValueError(str(e)) from None
    names = [p.name for p in model.ports]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate port names: {sorted(dupes)}")
    for p in model.ports:
        if not p.name.strip():
            raise ValueError("every port needs a non-empty name")
    return model.model_dump(mode="json")


def validate_system_doc(doc: dict) -> dict:
    """Validate + normalize a system-template document."""
    import pydantic

    if not isinstance(doc, dict):
        raise ValueError(f"doc must be an object, got {type(doc).__name__}")
    try:
        model = _system_doc_model()(**doc)
    except pydantic.ValidationError as e:
        raise ValueError(str(e)) from None
    return model.model_dump(mode="json")
