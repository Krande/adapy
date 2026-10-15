"""Attribute-search hits written as IFC files -- one file per hit, rooted at the top of its tree.

WHAT A HIT IS, AND WHAT IS WRITTEN FOR IT. A hit of ``POST /scopes/{scope}/assets/search`` names a
node of a provider's published tree (``provider``, ``collection``, ``node_id``, ``full_name``) and
carries its ancestors (``path``, root first). The file written for it holds the hit's GEOMETRY and,
above it, one empty spatial level per ancestor (SITE -> ZONE -> ... -> hit), so a file opened on
its own still says where in the plant its steel sits.

A MESH EXPORT IS THE QUICK LOOK (``geometry: "mesh"``, a user decision): the hit's triangles as
its own provider published them, cut by the ordinary asset build (the one "Load into scene" runs),
written as ``<stem>.glb`` with the same sidecar -- no IFC, there is nothing in triangles to make
beams of. Its ``meta.geometry.kind`` is ``mesh``, and "request design geometry" on it runs a
one-hit design export into the same folder: ``<stem>.ifc`` beside ``<stem>.glb``. The two share a
stem and so a sidecar, which becomes the IFC's and names the mesh as ``derived.mesh``.

DESIGN GEOMETRY IS THE DESIGN SYSTEM'S OWN CSG (a user decision). It comes through the provider
seam every other "read a node into objects" path uses (``ada.assets.concepts``) from the provider
that publishes the E3D export (:data:`GEOMETRY_PROVIDER`), never from a mesh another provider
published for the same node. A hit from another provider (a mesh one) is re-addressed by NODE ID -- the
collections share ``fn-<sha1>`` ids derived from the full name -- and then by full name: the
geometry provider's spine either has a readable subject covering that node, or the hit has no
geometry to export, which is a per-file outcome (``no_geometry``), never a failed run.

PROVENANCE TRAVELS IN THE FILE, as IFC property sets, so a file copied out of the folder still
knows where it came from:

* ``ADA_Provenance`` on the site (the assembly): provider, collection, source scope, source
  revision, query (JSON), export time, exporter version, run id.
* ``ADA_Source`` on every level and on the hit: provider, scope, collection, node id, full name,
  source ref -- and on every geometry element that was matched to an index row. Each set alone is
  enough to find its node in the source tree; the levels' sets, root first, are its path.
* ``ADA_MatchedAttributes`` on the hit: the attributes the query filtered on, with the hit's values.
* ``ADA_SourceAttributes`` (optional, default on) on EVERY element written -- each level and each
  geometry element -- with the full attribute set of its attribute-index row, values typed (a value
  that parses as an integer is an IfcInteger, as a number an IfcReal, else text). Users download a
  folder of these and group across hundreds of files by plate thickness or material, so a value
  is written as the source wrote it, only typed, never reformatted.

and beside it, as ``<stem>.meta.json`` (``ada.files/meta@1``), which the viewer reads without
opening the IFC (``GET /scopes/{scope}/files/meta``).

Leaf module on purpose (no FastAPI, no database): the worker handler (``formats/export_search.py``)
and the tests call the same functions; the handler does the I/O.
"""

from __future__ import annotations

import json
import math
import pathlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "EXPORT_INDEX_FILENAME",
    "EXPORT_INDEX_SCHEMA",
    "EXPORT_SEARCH_KIND",
    "ElementRef",
    "GEOMETRY_DESIGN",
    "GEOMETRY_KINDS",
    "GEOMETRY_MESH",
    "GEOMETRY_PROVIDER",
    "META_SCHEMA",
    "NoGeometry",
    "apply_source_attributes",
    "element_sources",
    "export_file_key",
    "export_merged_key",
    "matched_attributes",
    "merged_mesh_elements",
    "meta_document",
    "mesh_elements",
    "meta_sidecar_key",
    "parse_ifc_provenance",
    "patch_step_description",
    "resolve_geometry",
    "safe_stem",
    "sidecar_key",
    "stamp_matched",
    "thumb_sidecar_key",
    "typed_attrs",
    "typed_value",
    "wrap_all_in_context",
    "wrap_in_context",
    "write_ifc",
]

#: The job kind -- a synthetic one: geometry comes from a provider through the storage facade.
EXPORT_SEARCH_KIND = "export_search_asset"
#: The provider whose concepts reader turns the E3D export into ada objects.
GEOMETRY_PROVIDER = "external-csg"
#: What an export writes per hit: ``design`` -- the design system's own geometry
#: (:data:`GEOMETRY_PROVIDER`) as IFC, exact, and slow when it has to be requested first; ``mesh``
#: -- the hit's triangles as the hit's OWN provider published them (the one whose attribute index
#: found it), built into a GLB: quick, for looking at, and no beams or plates, so no IFC. A mesh
#: file's ``meta.geometry.kind`` says which it is, and is what offers "request design geometry"
#: for it afterwards.
GEOMETRY_DESIGN = "design"
GEOMETRY_MESH = "mesh"
GEOMETRY_KINDS = (GEOMETRY_DESIGN, GEOMETRY_MESH)
META_SCHEMA = "ada.files/meta@1"
EXPORT_INDEX_SCHEMA = "ada.files/export@1"
#: The folder-level list of what an export wrote, in export order.
EXPORT_INDEX_FILENAME = "_export.json"

PSET_PROVENANCE = "ADA_Provenance"
PSET_SOURCE = "ADA_Source"
PSET_MATCHED = "ADA_MatchedAttributes"
PSET_ATTRS = "ADA_SourceAttributes"

_UNSAFE = re.compile(r"[^A-Za-z0-9._ -]+")
_INT_RE = re.compile(r"^[+-]?(0|[1-9][0-9]*)$")
_PIECE_SUFFIX = re.compile(r"_[lsf]\d+$")


class NoGeometry(LookupError):
    """The geometry provider publishes nothing readable that covers the hit -- said per file.

    ``reason`` says which of three cases it is, because they call for different answers:

    * ``unpublished`` -- the provider published nothing at all in the collection: the design
      system may well know the node, nobody has asked it yet;
    * ``listed`` -- the provider's tree lists the node (``node_id`` is the id it lists it under) but
      no geometry covering it was published: asking for that node is exactly the fix;
    * ``unknown`` -- the provider published a tree for the collection and the node is not in it: a
      request by node is unlikely to help, because the source did not export it.
    """

    def __init__(self, message: str, *, reason: str = "unknown", node_id: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.node_id = node_id


# --- keys ---------------------------------------------------------------------------------------


def safe_stem(full_name: str | None, fallback: str) -> str:
    """A file stem from a full name: printable, no path separators, at most 120 characters.
    ``/ALPHA/BEAM-1`` -> ``ALPHA_BEAM-1``."""
    text = str(full_name or "").strip().lstrip("/")
    stem = _UNSAFE.sub("_", text).strip(" ._")
    if not stem:
        stem = _UNSAFE.sub("_", str(fallback or "")).strip(" ._") or "node"
    return stem[:120]


def sidecar_key(key: str, suffix: str) -> str:
    """``x/y/A.ifc`` -> ``x/y/A<suffix>``: the file's LAST extension replaced. One rule for every
    sibling an export writes, so the IFC, its GLB and its STEP share one ``.meta.json`` and one
    ``.thumb.png``."""
    head, _, name = key.rpartition("/")
    stem = name.rsplit(".", 1)[0] if "." in name.lstrip(".") else name
    return f"{head}/{stem}{suffix}" if head else f"{stem}{suffix}"


def meta_sidecar_key(key: str) -> str:
    return sidecar_key(key, ".meta.json")


def thumb_sidecar_key(key: str) -> str:
    return sidecar_key(key, ".thumb.png")


def joint_thumb_key(key: str, n: int) -> str:
    """The thumbnail of the ``n``-th hit in a merged file: ``x/A.ifc`` -> ``x/A.j0003.thumb.png``.
    A ``.thumb.png`` like the file's own, so listings hide it and it goes with the file
    (``converters.keys.joint_thumb_keys``)."""
    return sidecar_key(key, f".j{n:04d}.thumb.png")


def clean_folder(folder: str) -> str:
    """A target folder as a scope-relative prefix: no leading/trailing slash, no ``..``, no
    internal namespace. Raises ValueError with the reason."""
    parts = [p for p in str(folder or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts:
        raise ValueError("the target folder is empty")
    if any(p == ".." for p in parts):
        raise ValueError("the target folder may not contain '..'")
    if parts[0].startswith("_") or parts[0] in ("assets", "versions"):
        raise ValueError(f"{parts[0]!r} is a reserved folder; choose another")
    return "/".join(parts)


def export_file_key(
    folder: str, collection: str, full_name: str | None, node_id: str, taken: set[str], ext: str = "ifc"
) -> str:
    """``<folder>/<collection>/<safe(full_name)>.<ext>``, unique within ``taken`` (which it extends):
    two full names that sanitise alike get the node id appended rather than overwrite each other."""
    base = f"{folder}/{safe_stem(collection, 'collection')}/{safe_stem(full_name, node_id)}"
    key = f"{base}.{ext}"
    if key in taken:
        key = f"{base}__{safe_stem(node_id, 'node')[:24]}.{ext}"
    n = 2
    while key in taken:
        key = f"{base}__{n}.{ext}"
        n += 1
    taken.add(key)
    return key


# --- values -------------------------------------------------------------------------------------


def typed_value(value: Any) -> Any:
    """A source value as the IFC writer should type it: integers stay (or become) ints, numbers
    floats, everything else text. A string with a leading zero (``007``) is an identifier, not a
    number, and stays text; so does ``nan``/``inf``."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
    text = value.strip()
    if _INT_RE.match(text):
        try:
            return int(text)
        except ValueError:
            return value
    if text and text[0] in "+-.0123456789" and not re.match(r"^[+-]?0\d", text):
        try:
            number = float(text)
        except ValueError:
            return value
        return number if math.isfinite(number) else value
    return value


def typed_attrs(attrs: Mapping[str, Any] | None) -> dict:
    """Every attribute typed (:func:`typed_value`), nulls dropped, keys verbatim."""
    out: dict[str, Any] = {}
    for key, value in (attrs or {}).items():
        typed = typed_value(value)
        if typed is None or typed == "":
            continue
        out[str(key)] = typed
    return out


def matched_attributes(query: Mapping[str, Any] | None, attrs: Mapping[str, Any] | None) -> dict:
    """The attributes the query filtered on (``where`` keys), with the hit's values."""
    keys: list[str] = []
    for w in (query or {}).get("where") or ():
        key = w.get("key") if isinstance(w, Mapping) else None
        if isinstance(key, str) and key not in keys:
            keys.append(key)
    attrs = attrs or {}
    return {k: typed_value(attrs[k]) for k in keys if k in attrs and attrs[k] is not None}


# --- geometry -----------------------------------------------------------------------------------


def resolve_geometry(index: Any, collection: str, hit: Mapping[str, Any], *, provider: str = GEOMETRY_PROVIDER):
    """``(manifest, node_id)`` of the geometry provider's readable publish covering ``hit``.

    ``index`` is an ``ada.clash.geometry_source.GeometrySourceIndex`` over the scope's storage. The
    node is looked up by the hit's id first (the providers share name-derived ids), then by the
    id its full name derives, then by label; the subject the node is its own subject of wins over
    the narrowest readable subject whose spine holds it. Raises :class:`NoGeometry` saying why.
    """
    from ada.clash.geometry_source import normalise_label

    catalog = index.catalog(collection, provider)
    if not catalog.spines:
        # Said as what is missing and how to get it: the user reading this per hit has just exported
        # from a search, whose index is filled by a different job than the tree the export needs.
        raise NoGeometry(
            f"provider {provider!r} has no published project tree for collection {collection!r} -- the search "
            f"index is filled separately; request the collection's tree from {provider!r} (the Sources tab's "
            f"collection request, or its sweep for the tree) and export again",
            reason="unpublished",
        )

    candidates: list[str] = []
    if hit.get("node_id"):
        candidates.append(str(hit["node_id"]))
    full_name = str(hit.get("full_name") or "").strip()
    if full_name:
        candidates.append(_node_id_for_full_name(full_name))
        norm = normalise_label(full_name)
        named = catalog.exact.get(norm) or catalog.folded.get(norm.casefold()) or {}
        if len(named) == 1:
            candidates.extend(named)
    seen: set[str] = set()
    for node_id in candidates:
        if node_id in seen:
            continue
        seen.add(node_id)
        own = next((s for s in catalog.spines if s.node == node_id and s.readable), None)
        if own is not None:
            return own.manifest, node_id
        covers = [s for s in catalog.spines if s.readable and any(i == node_id for i, _ in s.rows)]
        if covers:
            cover = min(covers, key=lambda s: (len(s.rows), s.subject))
            return cover.manifest, node_id
    listed = next((i for s in catalog.spines for i, _ in s.rows if i in seen), None)
    if listed is not None:
        raise NoGeometry(
            f"provider {provider!r} lists {full_name or hit.get('node_id')!r} in {collection!r} but publishes no "
            f"geometry covering it (publish its geometry first)",
            reason="listed",
            node_id=listed,
        )
    if not any(s.subject == collection for s in catalog.spines):
        # Node publishes without the collection's own tree say nothing about the nodes they leave out.
        raise NoGeometry(
            f"provider {provider!r} has published some nodes of {collection!r} but not its project tree, "
            f"and {full_name or hit.get('node_id')!r} is not among them -- request the collection's tree "
            f"from {provider!r} and export again",
            reason="unpublished",
        )
    raise NoGeometry(
        f"provider {provider!r} has no node {full_name or hit.get('node_id')!r} in {collection!r} -- "
        f"its geometry has not been exported from the design system",
        reason="unknown",
    )


def _node_id_for_full_name(full_name: str) -> str:
    """The name-derived node id every provider publishing a collection keyed by full names shares
    (``fn-`` + 20 hex of the SHA-1 of the trimmed full name)."""
    import hashlib

    return "fn-" + hashlib.sha1(full_name.strip().encode("utf-8")).hexdigest()[:20]


# --- the model ----------------------------------------------------------------------------------


@dataclass
class ElementRef:
    """One element written into the file, and how it is looked up in the attribute index."""

    obj: Any
    role: str  # "level" | "hit" | "part" | "member"
    node_id: str | None = None
    full_name: str | None = None
    source_ref: str | None = None
    #: The index row it was matched to, once matched.
    row: dict | None = None

    def lookup_names(self) -> list[str]:
        """Both spellings of the full name: an E3D reader strips the leading ``/`` that the index
        keeps, and a piece of a split member is named ``<name>_s<n>``."""
        names: list[str] = []
        for raw in (self.full_name, _PIECE_SUFFIX.sub("", self.full_name or "")):
            text = str(raw or "").strip()
            if not text:
                continue
            bare = text.lstrip("/")
            for n in (text, bare, f"/{bare}"):
                if n not in names:
                    names.append(n)
        return names


@dataclass
class WrappedModel:
    assembly: Any
    elements: list[ElementRef] = field(default_factory=list)

    @property
    def hit(self) -> ElementRef:
        return next(e for e in self.elements if e.role == "hit")

    @property
    def hits(self) -> list[ElementRef]:
        """Every hit, in the order they were wrapped (one, unless :func:`wrap_all_in_context`)."""
        return [e for e in self.elements if e.role == "hit"]


def _name(entry: Mapping[str, Any]) -> str:
    return str(entry.get("full_name") or entry.get("node_id") or "level")


def wrap_in_context(part: Any, hit: Mapping[str, Any], *, units: Any = None) -> WrappedModel:
    """``part`` (the hit's geometry, a bare ``ada.Part``) placed under one empty part per ancestor
    in ``hit["path"]`` (root first), in a fresh ``Assembly`` that IS the root level.

    Provider-neutral: the levels are named by full name (node id when there is none) and know
    their node ids, which is all the attribute lookup needs. With no path the assembly is named
    after the hit and holds it directly. The hit part keeps its own name -- the geometry
    provider's -- and its sub-parts and objects are listed as ``part`` / ``member`` elements.
    """
    return wrap_all_in_context([(part, hit)], units=units)


def _level_key(entry: Mapping[str, Any]) -> str:
    return str(entry.get("node_id") or entry.get("full_name") or "")


def wrap_all_in_context(
    items: Sequence[tuple[Any, Mapping[str, Any]]], *, name: str | None = None, units: Any = None
) -> WrappedModel:
    """Every ``(part, hit)`` of ``items`` placed as :func:`wrap_in_context` places one, in ONE
    assembly -- a whole collection's hits as one file.

    Ancestors are SHARED: two hits under the same zone hang off one zone part, matched by node id
    (full name when there is none), so the merged file's tree is the source tree cut down to the
    hits rather than one copy of the site per hit. When every hit has the same root, the assembly
    IS that root, as it is for one hit; otherwise it is named ``name`` and each root is a part of
    it. Elements are listed level by level as they are first met, each hit followed by its parts
    and members, so :attr:`WrappedModel.hits` is in ``items`` order.
    """
    import ada

    if not items:
        raise ValueError("nothing to wrap")
    paths = [[p for p in (hit.get("path") or ()) if isinstance(p, Mapping)] for _, hit in items]
    roots = {_level_key(path[0]) if path else None for path in paths}
    shared_root = len(roots) == 1 and None not in roots
    units = units or getattr(items[0][0], "units", "m")
    elements: list[ElementRef] = []
    levels: dict[tuple[str, ...], Any] = {}
    if shared_root:
        root = paths[0][0]
        asm = ada.Assembly(_name(root), units=units)
        elements.append(ElementRef(asm, "level", root.get("node_id"), root.get("full_name"), root.get("source_ref")))
        levels[(_level_key(root),)] = asm
    else:
        asm = ada.Assembly(_name(items[0][1]) if len(items) == 1 else str(name or "export"), units=units)
    used = {asm.name}

    for (part, hit), path in zip(items, paths):
        parent = asm
        chain: tuple[str, ...] = ()
        below = path
        if shared_root:
            chain = (_level_key(path[0]),)
            below = path[1:]
        for level in below:
            chain = chain + (_level_key(level),)
            child = levels.get(chain)
            if child is None:
                level_name = _name(level)
                if level_name in used:
                    level_name = f"{level_name} [{level.get('node_id')}]"
                used.add(level_name)
                child = ada.Part(level_name)
                parent.add_part(child)
                levels[chain] = child
                elements.append(
                    ElementRef(child, "level", level.get("node_id"), level.get("full_name"), level.get("source_ref"))
                )
            parent = child
        if part.name in used:
            part.name = f"{part.name} [{hit.get('node_id')}]"
        used.add(part.name)
        parent.add_part(part)
        elements.append(ElementRef(part, "hit", hit.get("node_id"), hit.get("full_name"), hit.get("source_ref")))

        for sub in part.get_all_subparts(include_self=False):
            elements.append(ElementRef(sub, "part", None, str(sub.name).split(" [", 1)[0]))
        for obj in part.get_all_physical_objects():
            meta = getattr(obj, "metadata", None) or {}
            ref = meta.get("RefNo") or meta.get("source_ref")
            elements.append(ElementRef(obj, "member", None, str(obj.name), str(ref) if ref else None))
    return WrappedModel(assembly=asm, elements=elements)


def export_merged_key(folder: str, collection: str, ext: str = "ifc") -> str:
    """``<folder>/<safe(collection)>.<ext>``: the one file a merged export writes per collection."""
    return f"{folder}/{safe_stem(collection, 'collection')}.{ext}"


def _set_pset(obj: Any, name: str, values: Mapping[str, Any]) -> None:
    clean = {k: v for k, v in values.items() if v is not None and v != ""}
    if not clean:
        return
    meta = getattr(obj, "metadata", None)
    if meta is None:
        obj.metadata = meta = {}
    meta[name] = clean


def _source_values(
    *,
    provider: str,
    collection: str,
    scope: str | None,
    node_id: Any,
    full_name: Any,
    source_ref: Any,
) -> dict:
    """One ``ADA_Source`` set. ``scope`` is the scope the search ran in, in its API path form
    (``project:<id>`` / ``shared``): a viewer that opens the file anywhere -- another scope, a
    download re-uploaded elsewhere -- can take ONE selected element back to its node in the source
    tree without reading the site's provenance or the sidecar."""
    return {
        "provider": provider,
        "scope": scope,
        "collection": collection,
        "node_id": node_id,
        "full_name": full_name,
        "source_ref": source_ref,
    }


def stamp_provenance(
    model: WrappedModel,
    *,
    provider: str,
    collection: str,
    provenance: Mapping[str, Any],
    matched: Mapping[str, Any],
    scope: str | None = None,
) -> None:
    """``ADA_Provenance`` on the site, ``ADA_Source`` on every level and the hit,
    ``ADA_MatchedAttributes`` on the hit. ``scope`` (the searched scope's path form) goes into
    every ``ADA_Source`` and, as ``source_scope``, into ``ADA_Provenance`` unless that already
    names one."""
    prov = dict(provenance)
    if scope and not prov.get("source_scope"):
        prov["source_scope"] = scope
    _set_pset(model.assembly, PSET_PROVENANCE, prov)
    for el in model.elements:
        if el.role in ("level", "hit"):
            _set_pset(
                el.obj,
                PSET_SOURCE,
                _source_values(
                    provider=provider,
                    collection=collection,
                    scope=scope,
                    node_id=el.node_id,
                    full_name=el.full_name,
                    source_ref=el.source_ref,
                ),
            )
    if matched:
        _set_pset(model.hit.obj, PSET_MATCHED, dict(matched))


def stamp_matched(element: ElementRef, matched: Mapping[str, Any]) -> None:
    """``ADA_MatchedAttributes`` on one hit -- what :func:`stamp_provenance` does for the only hit,
    for each hit of a merged file."""
    if matched:
        _set_pset(element.obj, PSET_MATCHED, dict(matched))


def apply_source_attributes(
    model: WrappedModel,
    rows: Iterable[Mapping[str, Any]],
    *,
    provider: str,
    collection: str,
    include_attributes: bool = True,
    scope: str | None = None,
) -> dict:
    """Match every element to an index row -- node id, then full name, then source ref -- and
    write its typed attributes as ``ADA_SourceAttributes`` (when ``include_attributes``). A matched
    member also gets ``ADA_Source`` (with ``scope``, as :func:`stamp_provenance` writes it).
    Returns ``{elements, with_attrs, without_attrs}``."""
    by_id: dict[str, Mapping] = {}
    by_name: dict[str, Mapping] = {}
    by_ref: dict[str, Mapping] = {}
    for row in rows:
        if row.get("node_id"):
            by_id.setdefault(str(row["node_id"]), row)
        if row.get("full_name"):
            by_name.setdefault(str(row["full_name"]).strip(), row)
        if row.get("source_ref"):
            by_ref.setdefault(str(row["source_ref"]), row)
    with_attrs = 0
    for el in model.elements:
        row = by_id.get(el.node_id or "") if el.node_id else None
        if row is None:
            row = next((by_name[n] for n in el.lookup_names() if n in by_name), None)
        if row is None and el.source_ref:
            row = by_ref.get(el.source_ref)
        el.row = dict(row) if row is not None else None
        if row is None:
            continue
        if el.role in ("part", "member"):
            _set_pset(
                el.obj,
                PSET_SOURCE,
                _source_values(
                    provider=provider,
                    collection=collection,
                    scope=scope,
                    node_id=row.get("node_id"),
                    full_name=row.get("full_name"),
                    source_ref=row.get("source_ref"),
                ),
            )
        attrs = typed_attrs(row.get("attrs"))
        if attrs:
            with_attrs += 1
            if include_attributes:
                _set_pset(el.obj, PSET_ATTRS, attrs)
    return {
        "elements": len(model.elements),
        "with_attrs": with_attrs,
        "without_attrs": len(model.elements) - with_attrs,
    }


def element_sources(model: WrappedModel) -> list[dict]:
    """``{name, guid, role, source}`` for every element that carries ``ADA_Source``, in model order
    (levels root first, the hit, then its parts and members) -- the sets as written, so a viewer that
    has the file's tree (names, nesting) but not its property sets can still take a selected element
    to its source node and build that node's path from its ancestors' sets. ``name`` is the IFC
    product's ``Name``; ``guid`` its ``GlobalId``."""
    out: list[dict] = []
    for el in model.elements:
        src = (getattr(el.obj, "metadata", None) or {}).get(PSET_SOURCE)
        if not src:
            continue
        out.append(
            {
                "name": str(getattr(el.obj, "name", "") or ""),
                "guid": getattr(el.obj, "guid", None),
                "role": el.role,
                "source": dict(src),
            }
        )
    return out


def mesh_elements(hit: Mapping[str, Any], *, provider: str, collection: str, scope: str | None) -> list[dict]:
    """:func:`element_sources` for a mesh GLB, which has no model to walk: one entry per ancestor
    (root first) and the hit, named by full name -- the names the GLB's own tree uses -- so a
    selected element still finds its node in the source tree."""
    out: list[dict] = []
    path = [p for p in (hit.get("path") or ()) if isinstance(p, Mapping)]
    for role, entry in [*(("level", p) for p in path), ("hit", hit)]:
        out.append(
            {
                "name": _name(entry),
                "guid": None,
                "role": role,
                "source": _source_values(
                    provider=provider,
                    collection=collection,
                    scope=scope,
                    node_id=entry.get("node_id"),
                    full_name=entry.get("full_name"),
                    source_ref=entry.get("source_ref"),
                ),
            }
        )
    return out


def merged_mesh_elements(
    hits: Sequence[Mapping[str, Any]], *, provider: str, collection: str, scope: str | None
) -> list[dict]:
    """:func:`mesh_elements` for a merged mesh GLB: every ancestor once, as its tree holds it once,
    then each hit."""
    out: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        for entry in mesh_elements(hit, provider=provider, collection=collection, scope=scope):
            ident = f"{entry['role']}:{entry['source'].get('node_id') or entry['name']}"
            if ident in seen:
                continue
            seen.add(ident)
            out.append(entry)
    return out


def write_ifc(model: WrappedModel, out_path: pathlib.Path) -> pathlib.Path:
    """The IFC, with the in-memory writer (the streamer only suits a freshly built model)."""
    from .selection_export import _mark_added

    _mark_added(model.assembly)
    model.assembly.to_ifc(str(out_path), streaming=False)
    return out_path


# --- the sidecar --------------------------------------------------------------------------------


def meta_document(
    *,
    file: str,
    fmt: str,
    created_by: str | None,
    source: Mapping[str, Any],
    query: Any,
    matched: Mapping[str, Any],
    run_id: str | None,
    attributes: Mapping[str, Any],
    derived: Mapping[str, Any],
    geometry: Mapping[str, Any] | None = None,
    created_at: str | None = None,
    elements: Sequence[Mapping[str, Any]] | None = None,
) -> dict:
    """``ada.files/meta@1`` -- what the viewer shows about a file without opening it.
    ``elements`` (:func:`element_sources`) is every element's ``ADA_Source``, by IFC name."""
    return {
        "schema": META_SCHEMA,
        "file": file,
        "format": fmt,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "created_by": created_by,
        "source": {
            "provider": source.get("provider"),
            "scope": source.get("scope"),
            "collection": source.get("collection"),
            "node_id": source.get("node_id"),
            "full_name": source.get("full_name"),
            "source_ref": source.get("source_ref"),
            "revision": source.get("revision"),
            "path": list(source.get("path") or []),
        },
        "geometry": dict(geometry) if geometry else None,
        "query": query,
        "matched": dict(matched),
        "run_id": run_id,
        "attributes": {
            "elements": int(attributes.get("elements") or 0),
            "with_attrs": int(attributes.get("with_attrs") or 0),
            "without_attrs": int(attributes.get("without_attrs") or 0),
        },
        # ``mesh`` links a design IFC to the quick-look GLB it was requested from; present only then.
        "derived": {
            **{k: derived.get(k) for k in ("glb", "step", "thumb")},
            **{k: derived[k] for k in ("mesh",) if derived.get(k)},
        },
        "elements": [dict(e) for e in elements or ()],
    }


# --- derived formats ------------------------------------------------------------------------------


def _step_string(text: str) -> str:
    """A STEP string literal: ASCII, quotes doubled."""
    ascii_text = text.encode("ascii", "replace").decode("ascii")
    return "'" + ascii_text.replace("'", "''") + "'"


def patch_step_description(path: pathlib.Path, lines: Sequence[str]) -> bool:
    """Put ``lines`` into the STEP header's ``FILE_DESCRIPTION`` description list. Rewrites only
    the header (everything up to ``ENDSEC;``); False when the file has no recognisable header."""
    data = path.read_bytes()
    end = data.find(b"ENDSEC;")
    if end < 0:
        return False
    header = data[:end].decode("latin-1")
    m = re.search(r"FILE_DESCRIPTION\s*\(\s*\((.*?)\)\s*,", header, re.S)
    if m is None:
        return False
    existing = m.group(1).strip()
    added = ",".join(_step_string(line) for line in lines)
    joined = f"{existing},{added}" if existing and existing != "''" else added
    header = header[: m.start(1)] + joined + header[m.end(1) :]
    path.write_bytes(header.encode("latin-1") + data[end:])
    return True


class GlbProvenance:
    """The ``to_dict`` shape :func:`ada.assets.build.patch_glb_provenance` writes into
    ``asset.extras.provenance``."""

    def __init__(self, doc: Mapping[str, Any]) -> None:
        self._doc = dict(doc)

    def to_dict(self) -> dict:
        return dict(self._doc)


# --- reading provenance back out of a file ---------------------------------------------------------


def parse_ifc_provenance(path: pathlib.Path) -> dict | None:
    """``{provenance, source, matched, elements}`` from an IFC's ``ADA_*`` property sets, or None.

    For a file whose sidecar is gone (copied, renamed by hand). ``source`` is the hit's: the
    ``ADA_Source`` set on the element that also carries ``ADA_MatchedAttributes``, else the
    deepest one. ``elements`` is every ``ADA_Source`` by product, as :func:`element_sources` lists
    them (``role`` is only known for the hit)."""
    try:
        import ifcopenshell
        import ifcopenshell.util.element as element_util
    except ImportError:
        return None
    f = ifcopenshell.open(str(path))
    provenance: dict = {}
    sources: list[dict] = []
    matched: dict = {}
    hit_source: dict | None = None
    elements: list[dict] = []
    for product in f.by_type("IfcObject"):
        psets = element_util.get_psets(product)
        if PSET_PROVENANCE in psets and not provenance:
            provenance = {k: v for k, v in psets[PSET_PROVENANCE].items() if k != "id"}
        src = psets.get(PSET_SOURCE)
        if src:
            clean = {k: v for k, v in src.items() if k != "id"}
            sources.append(clean)
            is_hit = PSET_MATCHED in psets
            if is_hit:
                hit_source = clean
                matched = {k: v for k, v in psets[PSET_MATCHED].items() if k != "id"}
            elements.append(
                {
                    "name": str(getattr(product, "Name", "") or ""),
                    "guid": getattr(product, "GlobalId", None),
                    "role": "hit" if is_hit else None,
                    "source": clean,
                }
            )
    if not provenance and not sources:
        return None
    return {
        "provenance": provenance,
        "source": hit_source or (sources[-1] if sources else {}),
        "matched": matched,
        "elements": elements,
    }
