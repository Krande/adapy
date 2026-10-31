"""A scope's model-building catalogue: the sections and materials its readable reference data offers
(``db/reference_data.py``), with an admin's material overrides merged in. See migrations 049 / 050.

PROVIDER-AGNOSTIC BY CONTRACT. A provider that can say what a reference entry IS puts it in the
entry's ``attrs`` (``ada.assets/reference-data@1``):

    ``section``   ``{type, dims: {h, w_top, w_btn, t_w, t_ftop, t_fbtn, r, wt, t}, units, gtype?,
                  source?}`` -- ``type`` is one of adapy's section types (``ada.sections.categories.
                  BaseTypes`` values: ``I``, ``BOX``, ``TUB``, ``T``, ``HP``, ``UNP``, ``CIRC``, ``FB``)
                  or ``PLATE`` (``dims.t`` its thickness); ``dims`` use adapy's ``Section`` parameter
                  names, in ``units`` (``mm`` unless it says otherwise).
    ``material``  ``{family, grade, rho, E, v, sig_y, sig_u, alpha, zeta, description, source?}`` --
                  whatever the source knows, in SI units (Pa, kg/m3, 1/K); ``family`` one of
                  :data:`FAMILIES`.

Nothing here knows a provider's catalogue; a row whose provider said nothing has ``section: null``
and is still listed (with its raw ``params``), because a person can still pick it.

MATERIAL OVERRIDES (``reference_overrides``) are an admin's values per (provider, collection, kind,
ref), kept apart from the provider's so a re-export never loses them, and merged over them on read:
every material row carries ``material.provider`` (what the source said), ``material.override`` (what
an admin set) and ``material.effective`` (the two merged, the override winning key by key).
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, Optional, Sequence

import asyncpg

from .reference_data import _text, _word

#: The material families a row or an override may name.
FAMILIES = ("carbon_steel", "stainless_steel", "aluminium", "other")
#: What an override may set, and how each value is checked: ``positive`` (a finite number > 0),
#: ``ratio`` (0 < v < 0.5), ``text``, or ``family`` (one of :data:`FAMILIES`).
MATERIAL_PROPS: dict[str, str] = {
    "family": "family",
    "grade": "text",
    "E": "positive",
    "v": "ratio",
    "rho": "positive",
    "sig_y": "positive",
    "sig_u": "positive",
    "alpha": "positive",
    "zeta": "positive",
}
#: The kinds an override may be set on, with the properties it may set.
OVERRIDABLE = {"material": MATERIAL_PROPS}
#: Which reference uses are model-building sections (a beam's profile, a plate's thickness).
SECTION_USES = ("section", "plate")

CATALOGUE_MAX_LIMIT = 5000
CATALOGUE_DEFAULT_LIMIT = 1000


class OverrideError(ValueError):
    """An override core cannot store as sent (400)."""


class CatalogueQueryError(ValueError):
    """A catalogue query core cannot answer as asked (400)."""


# --- overrides ----------------------------------------------------------------------------------


def _check_value(name: str, how: str, value: Any) -> Any:
    if how == "text":
        if not isinstance(value, str) or not value.strip():
            raise OverrideError(f"'{name}' must be a non-empty string")
        return value.strip()
    if how == "family":
        if value not in FAMILIES:
            raise OverrideError(f"'{name}' must be one of {', '.join(FAMILIES)}")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise OverrideError(f"'{name}' must be a finite number")
    value = float(value)
    if how == "ratio" and not 0.0 < value < 0.5:
        raise OverrideError(f"'{name}' must be between 0 and 0.5")
    if how == "positive" and value <= 0:
        raise OverrideError(f"'{name}' must be greater than zero")
    return value


def parse_override(body: Any) -> dict:
    """``{provider, collection, kind, ref, props, note?}`` checked: ``props`` names only what
    :data:`OVERRIDABLE` allows for the kind, each value checked; a ``null`` value drops the key (the
    provider's value shows through again). An override that sets nothing is refused -- delete it."""
    if not isinstance(body, dict):
        raise OverrideError("the body must be a JSON object")
    provider, collection, ref = _text(body.get("provider")), _text(body.get("collection")), _text(body.get("ref"))
    kind = _word(body.get("kind"))
    if not provider or not collection or not ref or not kind:
        raise OverrideError("'provider', 'collection', 'kind' and 'ref' are required")
    allowed = OVERRIDABLE.get(kind)
    if allowed is None:
        raise OverrideError(f"'{kind}' entries take no override; one of: {', '.join(OVERRIDABLE)}")
    raw = body.get("props")
    if not isinstance(raw, dict):
        raise OverrideError("'props' must be an object")
    unknown = sorted(set(raw) - set(allowed))
    if unknown:
        raise OverrideError(
            f"unknown propert{'y' if len(unknown) == 1 else 'ies'} {', '.join(unknown)}; allowed: {', '.join(allowed)}"
        )
    props = {k: _check_value(k, allowed[k], v) for k, v in raw.items() if v is not None}
    if not props:
        raise OverrideError("the override sets nothing; delete it instead")
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        raise OverrideError("'note' must be a string")
    return {
        "provider": provider.strip(),
        "collection": collection.strip().lower(),
        "kind": kind,
        "ref": ref,
        "props": props,
        "note": (note or "").strip() or None,
    }


def _override_wire(r) -> dict:
    props = r["props"]
    if isinstance(props, str):
        props = json.loads(props)
    return {
        "provider": r["provider"],
        "collection": r["collection"],
        "kind": r["kind"],
        "ref": r["ref"],
        "props": props or {},
        "note": r["note"],
        "updated_by": r["updated_by"],
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] is not None else None,
    }


async def put_override(pool: asyncpg.Pool, *, override: dict, updated_by: Optional[str]) -> dict:
    """Store (replace) one override, as :func:`parse_override` returned it."""
    row = await pool.fetchrow(
        """
        INSERT INTO reference_overrides (provider, collection, kind, ref, props, note, updated_by, updated_at)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, NOW())
        ON CONFLICT (provider, collection, kind, ref)
        DO UPDATE SET props = EXCLUDED.props, note = EXCLUDED.note, updated_by = EXCLUDED.updated_by, updated_at = NOW()
        RETURNING provider, collection, kind, ref, props, note, updated_by, updated_at
        """,
        override["provider"],
        override["collection"],
        override["kind"],
        override["ref"],
        json.dumps(override["props"]),
        override.get("note"),
        updated_by,
    )
    return _override_wire(row)


async def delete_override(pool: asyncpg.Pool, *, provider: str, collection: str, kind: str, ref: str) -> bool:
    status = await pool.execute(
        "DELETE FROM reference_overrides WHERE provider = $1 AND collection = $2 AND kind = $3 AND ref = $4",
        provider,
        collection.lower(),
        kind,
        ref,
    )
    return status.split()[-1] != "0"


async def list_overrides(
    pool: asyncpg.Pool, *, provider: Optional[str] = None, collection: Optional[str] = None
) -> list[dict]:
    """Every override (of a provider / collection), with ``orphaned`` true where no reference set
    of the collection lists the entry any more."""
    rows = await pool.fetch(
        """
        SELECT o.provider, o.collection, o.kind, o.ref, o.props, o.note, o.updated_by, o.updated_at,
               NOT EXISTS (SELECT 1 FROM reference_items i WHERE i.provider = o.provider
                           AND i.collection = o.collection AND i.kind = o.kind AND i.ref = o.ref) AS orphaned
        FROM reference_overrides o
        WHERE ($1::text IS NULL OR o.provider = $1) AND ($2::text IS NULL OR o.collection = $2)
        ORDER BY o.provider, o.collection, o.kind, o.ref
        """,
        provider,
        collection.lower() if collection else None,
    )
    return [{**_override_wire(r), "orphaned": bool(r["orphaned"])} for r in rows]


# --- merging ------------------------------------------------------------------------------------


def provider_material(attrs: dict) -> dict:
    """What the provider said about a material entry: its ``material`` object, falling back to the
    plain ``density`` / ``description`` a provider without one listed."""
    raw = attrs.get("material") if isinstance(attrs.get("material"), dict) else {}
    out = {k: raw[k] for k in (*MATERIAL_PROPS, "description") if raw.get(k) is not None}
    if "rho" not in out and isinstance(attrs.get("density"), (int, float)) and not isinstance(attrs["density"], bool):
        out["rho"] = float(attrs["density"])
    if "description" not in out and isinstance(attrs.get("description"), str) and attrs["description"].strip():
        out["description"] = attrs["description"].strip()
    return out


def merge_material(provider: dict, override: Optional[dict]) -> dict:
    """``{provider, override, effective}``: the override wins key by key; a key either names is in
    ``effective``. ``source`` says, per key of ``effective``, which side it came from."""
    override = dict(override or {})
    effective = {**provider, **override}
    source = {k: ("override" if k in override else "provider") for k in effective}
    return {"provider": provider, "override": override or None, "effective": effective, "source": source}


# --- the catalogue ------------------------------------------------------------------------------


def parse_catalogue_query(params: dict) -> dict:
    """``collection``, ``provider``, ``type`` (a section type, any case), ``use`` (``section`` /
    ``plate``), ``ref`` (exact), ``q`` (in ref or name, any case), ``limit`` (default 1000, at most
    5000), ``offset``."""
    try:
        limit = int(params.get("limit") or CATALOGUE_DEFAULT_LIMIT)
        offset = int(params.get("offset") or 0)
    except (TypeError, ValueError):
        raise CatalogueQueryError("'limit' and 'offset' must be integers") from None
    if limit < 1 or offset < 0:
        raise CatalogueQueryError("'limit' must be positive and 'offset' not negative")
    use = _word(params.get("use"))
    if params.get("use") and use not in SECTION_USES:
        raise CatalogueQueryError(f"'use' must be one of {', '.join(SECTION_USES)}")
    stype = _text(params.get("type"))
    collection = _text(params.get("collection"))
    return {
        "collection": collection.strip().lower() if collection else None,
        "provider": (_text(params.get("provider")) or "").strip() or None,
        "type": stype.strip().upper() if stype else None,
        "use": use,
        "ref": _text(params.get("ref")),
        "q": _text(params.get("q")),
        "limit": min(limit, CATALOGUE_MAX_LIMIT),
        "offset": offset,
    }


def _like(s: str) -> str:
    return "%" + s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


async def query_catalogue(pool: asyncpg.Pool, *, sets: Sequence[dict], what: str, query: dict) -> dict:
    """The ``sections`` or ``materials`` of the reference ``sets`` (as ``list_reference_sets``
    answered them for the reading scope), merged across them, ``ref`` order -- one row per (ref,
    collection): the same ref in two collections is two rows, each saying where it is from.

    ``{items, total, next_offset, collections}``. A section row: ``{ref, name, collection,
    provider, use, spec, section, gtype, params, plines}``; a material row: ``{ref, name,
    collection, provider, material: {provider, override, effective, source}, override_meta}``."""
    if what not in ("sections", "materials"):
        raise CatalogueQueryError("'what' must be sections or materials")
    chosen = [
        s
        for s in sets
        if (query["collection"] is None or s["collection"] == query["collection"])
        and (query["provider"] is None or s["provider"] == query["provider"])
    ]
    collections = [
        {"provider": s["provider"], "collection": s["collection"], "revision": s["revision"]} for s in chosen
    ]
    if not chosen:
        return {"items": [], "total": 0, "next_offset": None, "collections": collections}

    args: list[Any] = [
        [s["scope"] for s in chosen],
        [s["provider"] for s in chosen],
        [s["collection"] for s in chosen],
    ]

    def arg(v: Any) -> str:
        args.append(v)
        return f"${len(args)}"

    where: list[str] = []
    if what == "sections":
        where.append("i.kind = 'spco'")
        where.append(f"i.use = ANY({arg(list((query['use'],) if query['use'] else SECTION_USES))}::text[])")
        if query["type"]:
            where.append(f"upper(i.attrs->'section'->>'type') = {arg(query['type'])}")
    else:
        where.append("i.kind = 'material'")
    if query["ref"]:
        where.append(f"i.ref = {arg(query['ref'])}")
    if query["q"]:
        needle = arg(_like(query["q"]))
        where.append(f"(i.ref ILIKE {needle} OR i.name ILIKE {needle})")
    clause = " AND ".join(where)
    base = f"""
        FROM reference_items i
        JOIN unnest($1::text[], $2::text[], $3::text[]) AS s(scope, provider, collection)
          ON i.scope = s.scope AND i.provider = s.provider AND i.collection = s.collection
        LEFT JOIN reference_overrides o
          ON o.provider = i.provider AND o.collection = i.collection AND o.kind = i.kind AND o.ref = i.ref
        WHERE {clause}
    """
    total = await pool.fetchval(f"SELECT count(*) {base}", *args)
    rows = await pool.fetch(
        f"""
        SELECT i.provider, i.collection, i.kind, i.ref, i.name, i.owner, i.use, i.attrs,
               o.props AS override, o.updated_by, o.updated_at, o.note
        {base}
        ORDER BY i.ref, i.collection LIMIT {query['limit']} OFFSET {query['offset']}
        """,
        *args,
    )
    items = [_section_row(r) if what == "sections" else _material_row(r) for r in rows]
    end = query["offset"] + len(items)
    total = int(total or 0)
    return {"items": items, "total": total, "next_offset": end if end < total else None, "collections": collections}


def _json(v: Any) -> Any:
    return json.loads(v) if isinstance(v, str) else v


def _section_row(r) -> dict:
    attrs = _json(r["attrs"]) or {}
    section = attrs.get("section") if isinstance(attrs.get("section"), dict) else None
    return {
        "ref": r["ref"],
        "name": r["name"],
        "collection": r["collection"],
        "provider": r["provider"],
        "use": r["use"],
        "spec": r["owner"],
        "section": section,
        "gtype": attrs.get("gtype") or (section or {}).get("gtype"),
        "params": attrs.get("params") or [],
        "plines": attrs.get("plines") or [],
    }


def _material_row(r) -> dict:
    attrs = _json(r["attrs"]) or {}
    override = _json(r["override"])
    return {
        "ref": r["ref"],
        "name": r["name"],
        "collection": r["collection"],
        "provider": r["provider"],
        "material": merge_material(provider_material(attrs), override),
        "override_meta": (
            {
                "updated_by": r["updated_by"],
                "updated_at": r["updated_at"].isoformat() if r["updated_at"] is not None else None,
                "note": r["note"],
            }
            if override is not None
            else None
        ),
    }


async def overrides_by_ref(conn, *, provider: str, collection: str, kind: str, refs: Iterable[str]) -> dict[str, dict]:
    """``{ref: props}`` of the overrides of these entries -- what the reference browser merges."""
    rows = await conn.fetch(
        """
        SELECT ref, props FROM reference_overrides
        WHERE provider = $1 AND collection = $2 AND kind = $3 AND ref = ANY($4::text[])
        """,
        provider,
        collection,
        kind,
        list(refs),
    )
    return {r["ref"]: _json(r["props"]) or {} for r in rows}


__all__ = [
    "CatalogueQueryError",
    "FAMILIES",
    "MATERIAL_PROPS",
    "OVERRIDABLE",
    "OverrideError",
    "SECTION_USES",
    "delete_override",
    "list_overrides",
    "merge_material",
    "overrides_by_ref",
    "parse_catalogue_query",
    "parse_override",
    "provider_material",
    "put_override",
    "query_catalogue",
]
