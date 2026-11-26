"""Procedural-doc structures: resolving the ONE wire shape (``groups[]``) to the
builder's :class:`~ada.topology.entities.TopoStructure` objects, and back.

A procedural document names a structure two ways:

* ``groups[]`` (``CellGroup``: ``name``/``blueprint``/``include``/``origin``) —
  authored by the viewer's cellbuilder and read back by it, the shape every
  current producer and consumer (the viewer, the Excel-route engine, the
  plugin) uses;
* ``structures[]`` (``TopoStructure``: ``NAME``/``X``/``Y``/``Z``/``INCLUDE``,
  plus ``MODEL_INPUT``/``BLUEPRINT``/``CONDITION_NAME`` carried for a sibling
  tool) — a documented LEGACY input (an xlsx import's ``Structures`` sheet, or
  a document written before ``groups`` carried an origin).

:func:`structures_from_doc` merges the two by NAME (a group wins) into the
single list the builder places; :func:`groups_from_structures` is the inverse,
used by ``ProceduralBuilder.to_doc`` so a committed document is always written
back in the ONE shape (``groups``), never ``structures``."""

from __future__ import annotations

from ada.topology.entities import TopoStructure

__all__ = [
    "structures_from_doc",
    "group_blueprints_from_doc",
    "groups_from_structures",
    "structure_of",
]


def _strip_none(d: dict) -> dict:
    """Drop None-valued keys before ``TopoStructure(**d)`` — its ``X``/``Y``/``Z``
    are plain ``float`` fields (default 0.0, not None-default), so an explicit
    ``None`` from a sparsely-specified legacy entry would raise."""
    return {k: v for k, v in d.items() if v is not None}


def _origin_from(group: dict, legacy: dict) -> tuple[float, float, float]:
    """A structure's origin: the group's own ``origin`` tuple when present and
    well-formed, else the legacy ``Structures`` sheet's ``X``/``Y``/``Z``
    (default 0.0 for a missing/None coordinate)."""
    origin = group.get("origin")
    if origin is not None:
        try:
            x, y, z = origin
            return (float(x), float(y), float(z))
        except (TypeError, ValueError):
            pass  # malformed — fall through to the legacy columns (or 0,0,0)
    return (
        float(legacy.get("X") or 0.0),
        float(legacy.get("Y") or 0.0),
        float(legacy.get("Z") or 0.0),
    )


def structures_from_doc(doc: dict) -> list[TopoStructure]:
    """The doc's structures as :class:`TopoStructure` objects: ``groups[]``
    entries win by name; a legacy ``structures[]`` entry not named by any group
    is merged in under its own name, so an xlsx import's ``Structures`` sheet
    (or a document written before groups carried placement) still places. A
    group or legacy entry with no usable name is skipped — it names no
    structure for anything to be scoped to."""
    groups = {
        g["name"]: g
        for g in (doc.get("groups") or [])
        if isinstance(g, dict) and isinstance(g.get("name"), str) and g.get("name")
    }
    legacy = {
        s["NAME"]: s
        for s in (doc.get("structures") or [])
        if isinstance(s, dict) and isinstance(s.get("NAME"), str) and s.get("NAME")
    }

    out: list[TopoStructure] = []
    for name, g in groups.items():
        legacy_entry = legacy.get(name) or {}
        kwargs: dict = {
            "NAME": name,
            "INCLUDE": True if g.get("include") is None else bool(g.get("include")),
        }
        kwargs["X"], kwargs["Y"], kwargs["Z"] = _origin_from(g, legacy_entry)
        # Carried for the sibling tool's Structures sheet; adapy does not act on
        # these (see ``groups_from_structures`` / the ``TopoStructure`` docstring).
        # The legacy ``structures[]`` entry wins when present, but a document that
        # has already round-tripped once through ``to_doc`` carries these columns
        # directly on the GROUP itself (``groups_from_structures`` writes them
        # there, never back into a ``structures[]`` entry) — read them from there
        # too, or a second read-write hop silently drops them.
        for col in ("MODEL_INPUT", "BLUEPRINT", "CONDITION_NAME"):
            val = legacy_entry.get(col)
            if val is None:
                val = g.get(col)
            if val is not None:
                kwargs[col] = val
        out.append(TopoStructure(**kwargs))
    for name, s in legacy.items():
        if name in groups:
            continue  # a group of the same name already won
        out.append(TopoStructure(**_strip_none(s)))
    return out


def group_blueprints_from_doc(doc: dict) -> dict[str, str]:
    """``{group name: per-group blueprint_name}`` from ``groups[].blueprint`` —
    the structural blueprint (``"steel_stru"``/``"none"``) the built-in engine
    runs for that structure, distinct from the legacy ``structures[].BLUEPRINT``
    column (the sibling tool's own setting, carried but not read here)."""
    return {
        g["name"]: g["blueprint"]
        for g in (doc.get("groups") or [])
        if isinstance(g, dict)
        and isinstance(g.get("name"), str)
        and g.get("name")
        and isinstance(g.get("blueprint"), str)
    }


def groups_from_structures(
    structures: list[TopoStructure], group_blueprints: dict[str, str] | None = None
) -> list[dict]:
    """The ``groups[]`` entries ``ProceduralBuilder.to_doc`` writes for
    ``structures`` — the inverse of :func:`structures_from_doc`. A structure at
    the origin with no non-default legacy column is written as just
    ``{"name": ...}``, so a model with no placement/legacy data round-trips
    byte-identical to writing ``structures`` the old way."""
    group_blueprints = group_blueprints or {}
    defaults = TopoStructure.model_fields
    out: list[dict] = []
    for s in structures:
        group: dict = {"name": s.NAME}
        bp = group_blueprints.get(s.NAME)
        if bp:
            group["blueprint"] = bp
        if not s.INCLUDE:
            group["include"] = False
        origin = s.origin()
        if origin != (0.0, 0.0, 0.0):
            group["origin"] = origin
        # Nothing is lost for the sibling tool's columns: carried as extra group
        # keys whenever they differ from TopoStructure's own defaults.
        for col in ("MODEL_INPUT", "BLUEPRINT", "CONDITION_NAME"):
            val = getattr(s, col, None)
            if val != defaults[col].default:
                group[col] = val
        out.append(group)
    return out


def structure_of(item, spaces_by_name: dict) -> str | None:
    """The structure ``item`` (an equipment/opening/loft member) belongs to: its
    own ``STRUCTURE_NAME`` when set, else the ``STRUCTURE_NAME`` of the space its
    ``SPACE_NAME`` names — so an item that only names a space, not a structure,
    still lands in that space's frame instead of being silently dropped."""
    own = getattr(item, "STRUCTURE_NAME", None)
    if own:
        return own
    space_name = getattr(item, "SPACE_NAME", None)
    if not space_name:
        return None
    space = spaces_by_name.get(space_name)
    return getattr(space, "STRUCTURE_NAME", None) if space is not None else None
