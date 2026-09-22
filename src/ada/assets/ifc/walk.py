"""The geometry-free spatial walk -- ``IfcRelAggregates`` + ``IfcRelContainedInSpatialStructure``
from ``IfcProject`` down, and nothing else.

This is deliberately NOT ``PartImporter.load_hierarchies`` (``read/read_parts.py``) or the native
stream reader (``read/native_reader.py``): the first builds ``Part`` objects through the whole
typed read, the second yields a geometry blob per product. A publish job must not tessellate
anything, so this module never opens ``Representation`` beyond checking it is present, never
resolves a placement, and never reads a property set.

**The two edge kinds, folded into one parent pointer.** ``IfcRelAggregates`` (decomposition) and
``IfcRelContainedInSpatialStructure`` (spatial containment) answer different questions in the IFC
schema, but nothing downstream of this walk -- coverage, orphans, freshness, build scoping -- ever
behaves differently on which one placed a node under its parent (Decision 7's verdict on the
carrier survey: "Nothing in a two-tab browser behaves differently on the edge kind"). An
``IfcElementAssembly`` decomposes its members via ``IfcRelAggregates`` (it is not an
``IfcSpatialElement``, so it cannot use containment -- see ``write_ifc.py``'s
``add_related_elements_to_spatial_container``); a site/storey/space contains its products via
``IfcRelContainedInSpatialStructure``. Both become an ordinary parent pointer here.

**Branch vs leaf.** A branch is any product whose class is in ``SpatialTypes`` (site, building,
storey, space, spatial zone, element assembly, grid); a leaf is any product that carries a
``Representation`` -- geometry exists, even though this module never opens it. A product that is
neither (an ``IfcOpeningElement`` reached by accident, a type object, ...) is not a node the asset
browser can show or build, and is skipped rather than erred: the corpus is not obligated to be
complete, and a walk that raised on the first uninteresting entity would be unusable on a real
file.

**Node identity.** ``id`` is the entity's ``GlobalId`` VERBATIM -- 22 characters, alphabet
``0-9A-Za-z_$`` -- never hex-expanded (Decision 7 withdrew the first draft's hex expansion once
the key grammar's ``_SEGMENT_RE`` admitted ``$`` for exactly this). ``kind`` is the IFC class name
lower-cased (``ifcbuildingstorey``): provider vocabulary, opaque to core, read by the row glyph and
the branch-type filter.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Iterator

import ifcopenshell

from ada.base.ifc_types import SpatialTypes

__all__ = [
    "IfcNode",
    "IfcWalkError",
    "collection_index_nodes",
    "node_dicts",
    "subtree_nodes",
    "walk_full",
]

# Every IFC class name SpatialTypes knows, as the walk sees them: ifcopenshell entities spell
# their class exactly like this (``entity.is_a()``), so no case-folding is needed here -- only on
# the way OUT, into the projection's ``kind`` column.
_SPATIAL_CLASS_NAMES = frozenset(t.value for t in SpatialTypes)


class IfcWalkError(ValueError):
    """The file has no ``IfcProject``, or a requested node does not exist in it."""


@dataclass(frozen=True)
class IfcNode:
    """One entity, as the walk sees it. ``ifc_class`` is kept alongside the lower-cased ``kind``
    because the publish job needs the exact spelling to re-open the entity by GUID; ``kind`` is
    what travels into ``hierarchy.json``."""

    id: str
    parent: str | None
    label: str
    kind: str
    leaf: bool
    ifc_class: str


def _label(product: ifcopenshell.entity_instance) -> str:
    name = product.Name
    if name:
        return str(name)
    # No property-set fallback here on purpose -- this walk is metadata-free as well as
    # geometry-free (Decision 4 skips the optional `attributes` artefact for this phase); a
    # product without a Name is labelled by its own identity rather than left blank.
    return f"{product.is_a()} {product.GlobalId}"


def _has_representation(product: ifcopenshell.entity_instance) -> bool:
    return getattr(product, "Representation", None) is not None


def _is_spatial(product: ifcopenshell.entity_instance) -> bool:
    return product.is_a() in _SPATIAL_CLASS_NAMES


def _children(entity: ifcopenshell.entity_instance) -> Iterator[ifcopenshell.entity_instance]:
    """Every child reachable by ONE hop of either edge kind. Decomposition
    (``IsDecomposedBy``) covers a spatial container's sub-containers AND an element assembly's
    members; containment (``ContainsElements``) covers a spatial container's products. A leaf
    product normally has neither, but is walked too in case it decomposes further (a product with
    parts is still one leaf here -- see ``walk_full``)."""
    for rel in getattr(entity, "IsDecomposedBy", None) or ():
        yield from rel.RelatedObjects
    for rel in getattr(entity, "ContainsElements", None) or ():
        yield from rel.RelatedElements


def _walk_from(entity: ifcopenshell.entity_instance, parent_id: str | None, seen: set[int], out: list[IfcNode]) -> None:
    eid = entity.id()
    if eid in seen:
        # A product reachable by two paths (rare, but the schema does not forbid it) is one node
        # with the parent that reached it first -- a second path is not a second identity.
        return
    seen.add(eid)
    guid = entity.GlobalId
    is_spatial = _is_spatial(entity)
    has_repr = _has_representation(entity)
    if is_spatial:
        out.append(
            IfcNode(
                id=guid,
                parent=parent_id,
                label=_label(entity),
                kind=entity.is_a().lower(),
                leaf=False,
                ifc_class=entity.is_a(),
            )
        )
        for child in _children(entity):
            _walk_from(child, guid, seen, out)
    elif has_repr:
        out.append(
            IfcNode(
                id=guid,
                parent=parent_id,
                label=_label(entity),
                kind=entity.is_a().lower(),
                leaf=True,
                ifc_class=entity.is_a(),
            )
        )
        for child in _children(entity):
            _walk_from(child, guid, seen, out)
    # else: neither a spatial container nor a representable product -- not a node the browser can
    # show or build. Skipped rather than erred; its own children (if any) go unvisited too, which
    # is correct because nothing reached them through a node this walk recognises.


def walk_full(f: ifcopenshell.file) -> list[IfcNode]:
    """Walk every node reachable from ``IfcProject``, with REAL parent pointers throughout.

    This is the one full pass a publish job does; every other function in this module slices its
    result rather than re-walking the file, so a whole-file publish costs one traversal no matter
    how many roots it declares.
    """
    projects = f.by_type("IfcProject")
    if not projects:
        raise IfcWalkError("no IfcProject in this file -- nothing to walk")
    project = projects[0]
    out: list[IfcNode] = []
    seen: set[int] = set()
    for rel in getattr(project, "IsDecomposedBy", None) or ():
        for child in rel.RelatedObjects:
            _walk_from(child, None, seen, out)
    return out


def _children_index(nodes: Iterable[IfcNode]) -> dict[str | None, list[IfcNode]]:
    index: dict[str | None, list[IfcNode]] = {}
    for n in nodes:
        index.setdefault(n.parent, []).append(n)
    return index


def subtree_nodes(nodes: list[IfcNode], root_id: str) -> list[IfcNode]:
    """The root plus every descendant, in the SAME order a caller can find useful (root first,
    then a stable pre-order). The root's own row gets ``parent=None`` in the returned copy: a
    subtree spine's own top is not a claim about the node's real parent in the whole file --
    Decision 3's rule for a subtree document's top, applied to the one row it is about.

    Every node BELOW the root keeps its real parent, because the whole point of a subtree spine
    is to let the browser expand it without another fetch.
    """
    by_id = {n.id: n for n in nodes}
    root = by_id.get(root_id)
    if root is None:
        raise IfcWalkError(f"no node {root_id!r} in this walk (not reachable from IfcProject)")
    children = _children_index(nodes)
    out = [replace(root, parent=None)]
    stack = list(reversed(children.get(root_id, [])))
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(reversed(children.get(n.id, [])))
    return out


def collection_index_nodes(nodes: list[IfcNode], root_ids: Iterable[str]) -> list[IfcNode]:
    """Declared roots plus their DIRECT children only -- "depth 1 below the declared roots"
    (Decision 2a's projection split). A deeper collection index would duplicate what each root's
    own subtree spine already carries, fetched lazily on expand.

    A declared root's parent is forced to ``None`` UNLESS that parent is itself another declared
    root in this same index -- this corpus nests ``SiteB`` inside ``SiteA`` (adapy's writer always
    gives an ``Assembly`` its own ``IfcSite``, so two real sibling sites are not reachable through
    the public API; see ``corpus/make_plant_a.py``), and both are independently declared roots by
    default. Forcing ``SiteB``'s parent to ``None`` there would put the same id in this one
    document twice with two different, contradictory parents -- a duplicate no downstream reader
    is asked to resolve. Keeping its real parent instead means every id appears exactly once.
    """
    root_ids = list(root_ids)
    by_id = {n.id: n for n in nodes}
    children = _children_index(nodes)
    root_id_set = set(root_ids)
    out: list[IfcNode] = []
    seen: set[str] = set()
    for root_id in root_ids:
        root = by_id.get(root_id)
        if root is None:
            raise IfcWalkError(f"no node {root_id!r} in this walk (not reachable from IfcProject)")
        if root_id not in seen:
            emitted_parent = root.parent if root.parent in root_id_set else None
            out.append(replace(root, parent=emitted_parent))
            seen.add(root_id)
        for child in children.get(root_id, []):
            if child.id in seen:
                continue
            out.append(child)
            seen.add(child.id)
    return out


def declared_site_roots(nodes: list[IfcNode]) -> list[str]:
    """Every reachable ``IfcSite``, in walk order -- the default declared roots for a whole-file
    publish with no ``--root``/``--leaf`` (Decision 4: "Declared roots default to every IfcSite").
    """
    return [n.id for n in nodes if n.kind == "ifcsite"]


def ancestor_chain(nodes: list[IfcNode], node_id: str) -> list[str]:
    """Real parents of ``node_id``, nearest first, up to (not including) the walk's own roots.

    Used to find the nearest ALREADY-PUBLISHED ancestor when a scoped publish records
    ``hierarchy_revision`` -- it must walk the file's real structure, not any one subject's
    spine, because the nearest published ancestor may sit above whatever root a previous publish
    declared.
    """
    by_id = {n.id: n for n in nodes}
    out: list[str] = []
    current = by_id.get(node_id)
    if current is None:
        raise IfcWalkError(f"no node {node_id!r} in this walk (not reachable from IfcProject)")
    while current.parent is not None:
        out.append(current.parent)
        current = by_id[current.parent]
    return out


def node_dicts(nodes: Iterable[IfcNode], *, delivery_ids: Iterable[str] = ()) -> list[dict]:
    """``IfcNode`` -> the name-keyed dicts ``ada.assets.projection.build_hierarchy`` wants.

    ``delivery_ids`` marks which rows carry a ``build`` claim -- exactly the subject(s) this
    publish is writing a manifest for, never a row that merely APPEARS in a spine (Decision 4: a
    branch or leaf claims ``build`` only once it has its OWN manifest; every other row is
    navigational until its own publish gives it one).
    """
    wanted = set(delivery_ids)
    return [
        {
            "id": n.id,
            "parent": n.parent,
            "label": n.label,
            "kind": n.kind,
            "leaf": n.leaf,
            "delivery": "build" if n.id in wanted else "",
        }
        for n in nodes
    ]
