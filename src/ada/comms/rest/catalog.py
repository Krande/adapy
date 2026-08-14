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


# Built-in space-cell types and opening types — static so the API offers them in
# the cellbuilder's ``+ Cell`` / ``+ Opening`` pickers WITHOUT a live worker or
# importing ada. Each carries the default box extent a freshly-placed cell/opening
# is seeded with; an opening also carries its door/window subtype. Live workers
# (e.g. a capability engine) advertise more via ``procedural_cell_specs`` /
# ``procedural_opening_specs`` — kept in lock-step with the built-ins registered
# in ``ada.topo_model.cell_types``.
_CELL_TYPES = (
    {
        "slug": "room",
        "name": "Room",
        "description": "A generic rectangular space cell (5 x 5 x 3 m).",
        "size": [5.0, 5.0, 3.0],
        "metadata": {},
    },
)

_OPENING_TYPES = (
    {
        "slug": "door",
        "name": "Door",
        "description": "A full-height doorway cut to the floor (jambs + lintel + threshold).",
        "subtype": "door",
        "size": [0.9, 0.9, 2.1],
    },
    {
        "slug": "window",
        "name": "Window",
        "description": "A punched window opening at its placed height (jambs + head + sill).",
        "subtype": "window",
        "size": [1.2, 1.2, 1.0],
    },
    {
        "slug": "opening",
        "name": "Opening",
        "description": "A generic punched rectangular opening (jambs + head + sill).",
        "subtype": "opening",
        "size": [1.0, 1.0, 1.0],
    },
)

#: Fallback box extents when no catalog type resolves (mirrors the frontend
#: last-resort defaults in the cellbuilder controller).
DEFAULT_CELL_SIZE = [5.0, 5.0, 3.0]
DEFAULT_OPENING_SIZE = [1.0, 1.0, 2.0]


# Built-in structural blueprints, keyed by engine slug — static so the API
# offers them in the cellbuilder's Blueprint dropdown WITHOUT a live worker or
# importing ada. The ``adapy-default`` engine's ``steel_stru``/``none`` mirror the
# built-ins registered in ``ada.topo_model.blueprint_catalog`` (kept in lock-step
# by slug); live workers advertise more (and other engines' blueprints) via
# ``procedural_blueprint_specs``. The FIRST entry for an engine is its default.
# The engine slug is a literal here (this slim module must not import ada) — it
# matches ``ada.topo_model.engines.DEFAULT_ENGINE_SLUG``.
# Kept BY VALUE in lock-step with ``ada.topo_model.blueprint_catalog``'s
# ``STEEL_STRU_FIELDS`` (this slim image must not import ``ada``); parity asserted
# by ``tests/comms/rest/test_procedural_blueprints``.
_STEEL_STRU_FIELDS: tuple[dict, ...] = (
    {
        "name": "girder_sec",
        "label": "Girder section",
        "type": "enum",
        "default": "IPE200",
        "options": ["IPE200", "IPE300", "IPE400", "HEB200", "HEB300", "BG300x300x8x8", "BG400x300x12x16", "TUB200x10"],
    },
    {
        "name": "column_sec",
        "label": "Column section",
        "type": "enum",
        "default": "HEB200",
        "options": ["HEB200", "HEB300", "HEB400", "HEA300", "BG300x300x8x8", "BG400x400x12x12", "TUB200x10"],
    },
    {
        "name": "stringer_sec",
        "label": "Stringer section",
        "type": "enum",
        "default": "HP140x8",
        "options": ["HP140x8", "HP160x8", "HP200x10", "IPE200"],
    },
)

_PROCEDURAL_BLUEPRINTS: dict[str, tuple[dict, ...]] = {
    "adapy-default": (
        {
            "slug": "steel_stru",
            "name": "Steel structure",
            "description": "Decked steel structure framed over the space cells "
            "(girders, stringers, plate decks and walls).",
            "fields": list(_STEEL_STRU_FIELDS),
        },
        {
            "slug": "none",
            "name": "Raw boxes — no blueprint",
            "description": "Render the space cells as raw boxes with no structural blueprint.",
            "fields": [],
        },
    ),
}


# Built-in detailing engines — static so the API offers them in the Compile-
# settings "Detailing" dropdown WITHOUT a live worker or importing ada. Mirrors
# the built-ins registered in ``ada.topo_model.detailing_catalog`` (kept in
# lock-step by slug); live workers advertise more (and external engines) via
# ``procedural_detailing_engine_specs``. ``none`` (the default sentinel) is first.
_DETAILING_ENGINES: tuple[dict, ...] = (
    {
        "slug": "none",
        "name": "none",
        "description": "No detailing — the compiled structural model is left as-is (default).",
        "inprocess": True,
        "worker_capability": None,
        "joint_types": [],
    },
    {
        "slug": "adapy-default",
        "name": "adapy detailing",
        "description": "Built-in adapy detailing: girder–girder gusset, beam–column end plate and column base-plate joints.",
        "inprocess": True,
        "worker_capability": None,
        # Kept BY VALUE in lock-step with ``ada.topo_model.detailing_catalog``'s
        # ``ADAPY_DEFAULT_JOINT_TYPES`` (this slim image must not import ``ada``);
        # ``tests/comms/rest/test_procedural_detailing`` asserts the two match.
        "joint_types": [
            {
                "slug": "girder_gusset",
                "name": "Girder–girder gusset",
                "description": "Gusset plate + fillet weld beads at each I-girder to I-girder intersection.",
                "default_enabled": True,
                "fields": [
                    {"name": "weld_leg", "label": "Weld leg", "type": "number", "default": 6.0, "min": 3.0, "max": 20.0, "unit": "mm"},
                    {"name": "gusset_t", "label": "Gusset thickness", "type": "number", "default": 10.0, "min": 5.0, "max": 40.0, "unit": "mm"},
                ],
            },
            {
                "slug": "column_base_plate",
                "name": "Column base plate",
                "description": "Base plate + fillet welds (anchor bolts metadata-first) at each column footing.",
                "default_enabled": True,
                "fields": [
                    {"name": "overhang", "label": "Overhang", "type": "number", "default": 50.0, "min": 0.0, "max": 200.0, "unit": "mm"},
                    {"name": "weld_leg", "label": "Weld leg", "type": "number", "default": 6.0, "min": 3.0, "max": 20.0, "unit": "mm"},
                ],
            },
            {
                "slug": "box_to_box",
                "name": "Box-to-box clash cut",
                "description": "Boolean-cut the incoming box beam with the landing box member so they no longer clash (no weld/plate; opt-in).",
                "default_enabled": False,
                "fields": [
                    {"name": "clearance", "label": "Cut clearance", "type": "number", "default": 2.0, "min": 0.0, "max": 20.0, "unit": "mm"},
                ],
            },
        ],
    },
)


def builtin_detailing_engine_specs() -> list[dict]:
    """Specs for the built-in detailing engines (origin ``code``). Mirrors the
    ``ada.topo_model.detailing_catalog`` built-ins so the Detailing dropdown is
    never empty without a live worker."""
    out: list[dict] = []
    for d in _DETAILING_ENGINES:
        spec = dict(d)
        spec["joint_types"] = [dict(j) for j in d.get("joint_types", [])]
        out.append(spec)
    return out


# NOTE: adapy hardcodes NO external (Tier-B, out-of-process) detailing engines.
# An external engine is discovered ONLY from a live capability worker's heartbeat:
# the worker's ``ADA_WORKER_PRELOAD`` module registers the engine via
# ``ada.topo_model.register_detailing_engine`` (slug + ``entrypoint`` +
# ``worker_capability`` + joint types) at import, so ``detailing_engine_specs()``
# picks it up and the heartbeat advertises it (see ``_resolve_detailing_engine`` and
# the ``/detailing-engines`` endpoint, which union the built-ins with live specs).
# The engine is therefore selectable/routable whenever its pool is online and
# absent otherwise — no engine-specific identity lives in this repo.


def builtin_procedural_blueprint_specs(engine: str) -> list[dict]:
    """Specs for an engine's built-in structural blueprints (origin ``code``).
    Empty for an engine with no static built-ins (its blueprints come from a live
    worker's advertisement instead)."""
    return [dict(d) for d in _PROCEDURAL_BLUEPRINTS.get(engine, ())]


def builtin_cell_specs() -> list[dict]:
    """Specs for the built-in space-cell types (origin ``code``)."""
    return [dict(d) for d in _CELL_TYPES]


def builtin_opening_specs() -> list[dict]:
    """Specs for the built-in opening types (origin ``code``)."""
    return [dict(d) for d in _OPENING_TYPES]


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
    import re
    from typing import List, Literal, Optional

    from pydantic import BaseModel, Field, conlist, field_validator

    Vec3 = conlist(float, min_length=3, max_length=3)
    _HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

    class CatalogPort(BaseModel):
        name: str
        position: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 0.0])
        direction_vector: Vec3 = Field(default_factory=lambda: [0.0, 0.0, 1.0])
        direction: Literal["IN", "OUT", "INOUT"] = "INOUT"
        category: Literal["process", "electrical", "signal"] = "process"
        # Optional per-port colour override as ``#rrggbb``; ``None`` means the
        # frontend derives the colour from ``category``.
        color: Optional[str] = None

        @field_validator("color")
        @classmethod
        def _check_color(cls, v):
            if v is None:
                return v
            v = v.strip().lower()
            if not _HEX_RE.match(v):
                raise ValueError(f"color must be a '#rrggbb' hex string, got {v!r}")
            return v

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
        # Whether the linked CAD asset is authored in adapy's Z-up convention.
        # True (default) = take the asset verbatim (Z is height). False = the CAD
        # is glTF-spec Y-up and gets re-oriented Y-up→Z-up before measuring and
        # splicing. Only meaningful for mesh assets (.glb/.gltf/.stl/.obj).
        cad_z_up: bool = True
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


def resync_target_doc(archetype_doc: dict, stored_doc: dict, has_cad: bool) -> dict:
    """The doc to write when resyncing an equipment type to its code archetype.

    A CAD-backed type's geometry — ``bbox``/``cog`` inferred from its CAD asset,
    and ``ports`` the user aligned to that CAD — must be PRESERVED across a resync
    (which runs on every model open); otherwise an inferred bbox reverts to the
    archetype default cube every open, and aligned ports snap back. For such a
    type, keep those fields from the stored doc and take only the non-geometry
    code fields (mass, ifc_element_class) from the archetype. ``cad_z_up`` is a
    CAD property (not in the code archetype) so it's preserved too. A type with
    no CAD resyncs fully to the archetype."""
    if not has_cad:
        return archetype_doc
    return {
        **archetype_doc,
        "bbox": stored_doc.get("bbox", archetype_doc.get("bbox")),
        "cog": stored_doc.get("cog", archetype_doc.get("cog")),
        "ports": stored_doc.get("ports", archetype_doc.get("ports")),
        "cad_z_up": stored_doc.get("cad_z_up", archetype_doc.get("cad_z_up", True)),
    }


def summarize_equipment_doc_changes(old_doc: dict, old_name: str, new_doc: dict, new_name: str) -> list[str]:
    """Human-readable list of what changed between an equipment catalog entry and
    the code archetype it's being resynced to — so the resync summary can tell the
    user exactly what moved (a new port, a corrected nozzle height, a mass change)
    rather than just a count. Ports are diffed by name; scalars are rounded so a
    float-format wobble doesn't read as a change."""

    def _r(v):
        if isinstance(v, (int, float)):
            return round(float(v), 4)
        if isinstance(v, (list, tuple)):
            return [_r(x) for x in v]
        return v

    changes: list[str] = []
    if old_name != new_name:
        changes.append(f"name: {old_name!r} → {new_name!r}")
    for key in ("mass", "ifc_element_class", "bbox", "cog"):
        if _r(old_doc.get(key)) != _r(new_doc.get(key)):
            changes.append(f"{key}: {_r(old_doc.get(key))} → {_r(new_doc.get(key))}")

    old_ports = {p.get("name"): p for p in (old_doc.get("ports") or [])}
    new_ports = {p.get("name"): p for p in (new_doc.get("ports") or [])}
    for name in new_ports.keys() - old_ports.keys():
        changes.append(f"added port {name!r}")
    for name in old_ports.keys() - new_ports.keys():
        changes.append(f"removed port {name!r}")
    for name in old_ports.keys() & new_ports.keys():
        op, np_ = old_ports[name], new_ports[name]
        for field in ("position", "direction_vector", "direction", "category"):
            if _r(op.get(field)) != _r(np_.get(field)):
                changes.append(f"port {name!r} {field}: {_r(op.get(field))} → {_r(np_.get(field))}")
    return changes


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


@lru_cache(maxsize=1)
def _engine_doc_model():
    from typing import List, Literal, Optional

    from pydantic import BaseModel, model_validator

    class ProceduralEngineDoc(BaseModel):
        # ``builtin`` = the in-repo adapy engine (no external code); ``wheel`` = an
        # external engine cloned+built into a pyodide wheel; ``server`` = an engine
        # with native deps that only runs in a server worker (never in the browser).
        kind: Literal["builtin", "wheel", "server"] = "builtin"
        # Source repo + git ref an external engine is built from (required for
        # non-builtin kinds). The deploy KEY is never stored here — only the name
        # of a Vault-backed secret the build worker reads.
        repo_url: Optional[str] = None
        ref: Optional[str] = None
        deploy_key_secret: Optional[str] = None
        # Dotted ``module:callable`` entrypoint with signature ``compile(doc) -> bytes``.
        entrypoint: Optional[str] = None
        # Extra deps the browser must micropip-install for this engine's wheel.
        pyodide_deps: List[str] = []
        # ``kind:server`` routing: the worker-pool capability tag that has this
        # engine (+ its deps) pre-installed. The compile job is routed to a worker
        # advertising this tag; None = the default pool (only for engines whose
        # package is already in the base worker image).
        worker_capability: Optional[str] = None
        # Optional pip specs a server worker installs for this engine (lightweight
        # PyPI extras). Heavy conda/native deps must be baked into the capability
        # image instead — they are not pip-installable at runtime.
        deps: List[str] = []
        # Built wheel pointer under the hidden _engines/ prefix (set by the build
        # worker; ignored/overwritten on user commits).
        wheel_key: Optional[str] = None

        @model_validator(mode="after")
        def _check(self):
            if self.kind in ("wheel", "server"):
                if not self.repo_url:
                    raise ValueError(f"{self.kind!r} engine requires 'repo_url'")
                if not self.entrypoint:
                    raise ValueError(f"{self.kind!r} engine requires 'entrypoint' (module:callable)")
            if self.entrypoint is not None and ":" not in self.entrypoint:
                raise ValueError("entrypoint must be a dotted 'module:callable' path")
            return self

    return ProceduralEngineDoc


def validate_engine_doc(doc: dict) -> dict:
    """Validate + normalize a procedural-engine manifest document."""
    import pydantic

    if not isinstance(doc, dict):
        raise ValueError(f"doc must be an object, got {type(doc).__name__}")
    try:
        model = _engine_doc_model()(**doc)
    except pydantic.ValidationError as e:
        raise ValueError(str(e)) from None
    return model.model_dump(mode="json")
