"""Array-backed container facades over a shared :class:`MeshArrays` store.

``ArrayNodes``/``ArrayElements`` subclass the object-model ``Nodes``/``FemElements``
(so ``isinstance`` and every type check keep working) but store nothing per-entity —
they serve lazy proxies from one store shared via the owning ``FEM``. The object-model
containers are left completely untouched; these only run when
``Config().meshing_array_backed`` is on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.api.containers.nodes import Nodes
from ada.api.mesh.store import MeshArrays
from ada.config import Config, logger
from ada.core.vector_utils import points_in_cylinder, vector_length
from ada.fem.containers import FemElements, LazyElemSeq

if TYPE_CHECKING:
    from ada.api.mesh.proxies import NodeProxy


class ArrayNodes(Nodes):
    def __init__(self, store: MeshArrays, parent=None):
        self._store = store
        self._parent = parent
        if parent is not None:
            store._fem = parent
        self._point_tol = Config().general_point_tol
        self._sorted = None  # lazy lexsorted row order (by x,y,z)

    # ── construction ─────────────────────────────────────────────────────
    @classmethod
    def from_nodes(cls, nodes, parent=None) -> "ArrayNodes":
        return cls(MeshArrays.from_nodes(list(nodes)), parent)

    @property
    def store(self) -> MeshArrays:
        return self._store

    @property
    def _sorted_rows(self) -> np.ndarray:
        if self._sorted is None:
            c = self._store.coords
            if c.shape[0] == 0:
                self._sorted = np.zeros(0, dtype=np.int64)
            else:
                self._sorted = np.lexsort((c[:, 2], c[:, 1], c[:, 0]))
        return self._sorted

    def _invalidate_order(self):
        self._sorted = None

    # ── sequence protocol ────────────────────────────────────────────────
    def __len__(self) -> int:
        return self._store.n_nodes

    def __iter__(self) -> Iterable["NodeProxy"]:
        for r in self._sorted_rows:
            yield self._store.node_proxy(int(r))

    def __getitem__(self, index):
        rows = self._sorted_rows
        if isinstance(index, slice):
            return Nodes([self._store.node_proxy(int(r)) for r in rows[index]])
        return self._store.node_proxy(int(rows[index]))

    def __contains__(self, item) -> bool:
        return self._store.has_node(item.id) and self._store.node_proxy_by_id(item.id) == item

    def __eq__(self, other) -> bool:
        if not isinstance(other, Nodes):
            return NotImplemented
        return list(self) == list(other)

    def __add__(self, other: "Nodes") -> "Nodes":
        merged = list(self) + list(other)
        return ArrayNodes.from_nodes(merged, self.parent)

    def __repr__(self) -> str:
        return f"ArrayNodes({len(self)}, min_id: {self.min_nid}, max_id: {self.max_nid})"

    # ── lookups ──────────────────────────────────────────────────────────
    def from_id(self, nid: int):
        if not self._store.has_node(nid):
            raise ValueError(f'The node id "{nid}" is not found')
        return self._store.node_proxy_by_id(nid)

    def index(self, item) -> int:
        rows = self._sorted_rows
        target = self._store.node_index(item.id)
        hits = np.nonzero(rows == target)[0]
        if hits.size == 0:
            raise ValueError(f"{item!r} not found")
        return int(hits[0])

    def count(self, item) -> int:
        return int(item in self)

    # ── properties ───────────────────────────────────────────────────────
    @property
    def dmap(self) -> dict:
        return {self._store.node_id(r): self._store.node_proxy(r) for r in range(self._store.n_nodes)}

    @property
    def nodes(self) -> list:
        return list(self)

    @property
    def max_nid(self) -> int:
        return int(self._store.node_ids.max()) if self._store.n_nodes else 0

    @property
    def min_nid(self) -> int:
        return int(self._store.node_ids.min()) if self._store.n_nodes else 0

    def bbox(self):
        return self._store.bbox()

    def vol_cog(self):
        bx, by, bz = self.bbox()
        return ((bx[0] + bx[1]) / 2, (by[0] + by[1]) / 2, (bz[0] + bz[1]) / 2)

    def to_np_array(self, include_id: bool = False) -> np.ndarray:
        if include_id:
            return np.column_stack([self._store.node_ids.astype(float), self._store.coords])
        return self._store.coords.copy()

    def to_fem_nodes(self):
        return self._store.to_fem_nodes()

    # ── bulk ops ─────────────────────────────────────────────────────────
    def renumber(self, start_id: int = 1, renumber_map: dict[int, int] | None = None) -> None:
        self._store.renumber_nodes(start_id=start_id, renumber_map=renumber_map)

    def move(self, move=None, rotate=None) -> None:
        if rotate is not None:
            self._store.rotate(rotate.to_rot_matrix(), np.array(rotate.origin))
        if move is not None:
            self._store.translate(np.array(move))
        self._invalidate_order()

    def rounding_node_points(self, precision: int = None) -> None:
        if precision is None:
            precision = Config().general_precision
        self._store.coords = np.round(self._store.coords, precision)
        self._store._bbox = None
        self._invalidate_order()

    # ── queries ──────────────────────────────────────────────────────────
    def get_by_volume(self, p=None, vol_box=None, vol_cyl=None, tol=None, single_member=False):
        if tol is None:
            tol = self._point_tol
        p_arr = np.array(p) if p is not None else None
        if p_arr is not None and vol_box is None and vol_cyl is None:
            vol_min, vol_max = p_arr - tol, p_arr + tol
        elif vol_box is not None and p_arr is not None:
            vol_min, vol_max = np.array(p), np.array(vol_box)
        elif vol_cyl is not None and p_arr is not None:
            r, h, t = vol_cyl
            vol_min = np.array([p_arr[0] - r - tol, p_arr[1] - r - tol, p_arr[2] - tol])
            vol_max = np.array([p_arr[0] + r + tol, p_arr[1] + r + tol, p_arr[2] + tol + h])
        else:
            raise Exception("No valid search input provided. None is returned")

        rows = self._store.rows_in_box(vol_min, vol_max)
        if vol_cyl is not None:
            r, h, t = vol_cyl
            pt1, pt2 = p_arr + np.array([0, 0, -h]), p_arr + np.array([0, 0, h])
            keep = []
            for rr in rows:
                pnt = self._store.coords[rr]
                if t == r:
                    if points_in_cylinder(pt1, pt2, r, pnt):
                        keep.append(rr)
                else:
                    if points_in_cylinder(pt1, pt2, r + t, pnt) and not points_in_cylinder(pt1, pt2, r - t, pnt):
                        keep.append(rr)
            rows = keep

        # order matches the (x,y,z) lexsort the object path uses
        rows = sorted(rows, key=lambda rr: tuple(self._store.coords[rr]))
        result = [self._store.node_proxy(int(rr)) for rr in rows]
        if not result:
            logger.info(f"No nodes found in volume {vol_min}, {vol_max}, tol={tol}")
            return result
        if single_member:
            return result[0]
        return result

    # ── edits ────────────────────────────────────────────────────────────
    def add(self, node, point_tol: float = None, allow_coincident: bool = False):
        tol = point_tol if point_tol is not None else self._point_tol
        p = np.asarray(node.p, dtype=float)
        if self._store.n_nodes and not allow_coincident:
            for rr in self._store.rows_in_box(p - tol, p + tol):
                if vector_length(self._store.coords[rr] - p) < tol:
                    return self._store.node_proxy(int(rr))
        nid = node.id
        if nid is None or self._store.has_node(nid):
            nid = (self.max_nid + 1) if self._store.n_nodes else 1
        row = self._store.add_node(p, nid)
        self._invalidate_order()
        proxy = self._store.node_proxy(row)
        if proxy.parent is None:
            proxy.parent = self.parent
        return proxy

    def remove(self, nodes) -> None:
        from ada.api.nodes import Node

        nodes_list = [nodes] if isinstance(nodes, Node) else list(nodes)
        rows = [self._store.node_index(n.id) for n in nodes_list if self._store.has_node(n.id)]
        self._store.remove_nodes(rows)
        self._invalidate_order()
        self.renumber()

    def remove_standalones(self) -> None:
        self.remove([n for n in self if not n.has_refs])

    def merge_coincident(self, tol: float = None) -> None:
        from ada.fem.utils import replace_node

        tol = tol if tol is not None else self._point_tol
        for n in list(self):
            if not n.has_refs:
                continue
            if not self._store.has_node(n.id):
                continue
            dups = sorted(
                [m for m in self.get_by_volume(tuple(n.p), tol=tol) if m.id != n.id],
                key=lambda x: len(x.refs),
            )
            if dups:
                primary = max([n] + dups, key=lambda x: len(x.refs))
                for dup in dups:
                    replace_node(dup, primary)
                    self.remove(dup)
        self._invalidate_order()


class ArrayElements(FemElements):
    def __init__(self, store: MeshArrays, fem_obj=None):
        self._store = store
        self._fem_obj = fem_obj
        if fem_obj is not None:
            store._fem = fem_obj
        self._sort_funcs = []
        # Special elements that don't sit in the array blocks (Mass / Spring /
        # Connector). Few in number, kept as objects; the millions of structural
        # elements live in the store's blocks.
        self._overflow: list = []
        # Special elements whose element row *did* get packed into a block, because
        # ``MeshArrays.from_fem`` groups by ``Elem.type`` and a Mass/Spring/Connector has
        # a MassTypes/SpringTypes/ConnectorTypes type like any other. An
        # ``ElemArrayBlock`` holds connectivity + ids and has nowhere to keep a mass
        # value, a spring stiffness or a connector section/csys, so the object is
        # retained here beside its packed row rather than dropped: ``masses`` /
        # ``springs`` / ``connectors`` read both lists (see ``_iter_specials``). The row
        # itself stays in the block, so iteration, ``len()``, connectivity and every
        # consumer that walks the blocks are byte-for-byte unaffected.
        self._packed_specials: list = []
        self._warned_lost_specials = False

    @property
    def store(self) -> MeshArrays:
        return self._store

    def __len__(self) -> int:
        return self._store.n_elems() + len(self._overflow)

    def __repr__(self) -> str:
        by_type = ", ".join(f'"{ctype}": {len(blk)}' for ctype, blk in self._store.blocks.items())
        if self._overflow:
            extra = f"overflow: {len(self._overflow)}"
            by_type = f"{by_type}, {extra}" if by_type else extra
        return f"ArrayElements(Elements: {len(self)}, By Type: {by_type or 'none'})"

    def __iter__(self):
        yield from self._store.iter_elem_proxies()
        yield from self._overflow

    def __contains__(self, item) -> bool:
        try:
            self._store.elem_loc(item.id)
            return True
        except ValueError:
            return any(e.id == item.id for e in self._overflow)

    def __getitem__(self, index):
        return list(self)[index]

    def __add__(self, other):
        # Mirror FemElements.__add__: renumber the incoming elements above the current
        # max so appended elements (e.g. mass objects, which share an id with the
        # MASS-type Elem find_bnmass already added) don't collide.
        if hasattr(other, "renumber"):
            other.renumber(self.max_el_id + 1)
        for e in other:
            self.add(e)
        return self

    def from_id(self, el_id: int):
        try:
            return self._store.elem_proxy_by_id(el_id)
        except ValueError:
            for e in self._overflow:
                if e.id == el_id:
                    return e
            raise ValueError(f'The elem id "{el_id}" is not found')

    def add(self, elem, skip_grouping=False):
        if elem.id is None:
            elem._el_id = self.max_el_id + 1
        if elem.parent is None:
            elem.parent = self._fem_obj
        self._overflow.append(elem)
        return elem

    @property
    def max_el_id(self) -> int:
        store_max = 0
        for blk in self._store.blocks.values():
            if blk.el_ids.size:
                store_max = max(store_max, int(blk.el_ids.max()))
        ov_max = max((e.id for e in self._overflow if e.id is not None), default=0)
        return max(store_max, ov_max)

    @property
    def min_el_id(self) -> int:
        # Mirror of max_el_id — the object FemElements.min_el_id reads self._idmap, which the
        # array container doesn't have. Used by FEM.__add__ to decide whether to renumber.
        mins = [int(blk.el_ids.min()) for blk in self._store.blocks.values() if blk.el_ids.size]
        ov = [e.id for e in self._overflow if e.id is not None]
        candidates = mins + ov
        return min(candidates) if candidates else 0

    def build_sets(self):
        """Create id-backed element sets from each element's ``elset`` name (mirrors
        the object FemElements.build_sets, which sections/masses reference by name)."""
        from collections import defaultdict

        from ada.fem import FemSet

        by_elset: dict[str, list[int]] = defaultdict(list)
        for e in self:
            es = e.elset
            if es is None:
                continue
            name = es if isinstance(es, str) else getattr(es, "name", None)
            if name:
                by_elset[name].append(e.id)
        for name, ids in by_elset.items():
            if self._fem_obj is not None:
                self._fem_obj.sets.add(FemSet(name, ids, "elset", parent=self._fem_obj))

    # ── the special (Mass / Spring / Connector) elements ─────────────────
    def _iter_specials(self):
        """Every special element this container holds, wherever it lives.

        Two homes, both legitimate: ``_overflow`` (anything handed to ``add()`` — what
        the readers do) and ``_packed_specials`` (a row packed into a block by
        ``to_array_backed``, with the object kept alongside). Reading only ``_overflow``
        is what made a converted model's masses vanish from ``fem.elements.masses``, and
        with them the whole BNMASS / ``*Mass`` / NODEMASS block of the exported deck.
        """
        yield from self._overflow
        yield from self._packed_specials

    def _warn_on_unbacked_special_blocks(self) -> None:
        """Warn once if the store holds mass/spring/connector rows that no object backs.

        Such a row is an id, a type and a node reference — the value that makes it a mass
        (or a stiffness, or a connector section) is not in the array store and cannot be
        recovered from it, so every writer that emits those records has to skip it. Say so
        rather than exporting a deck that quietly dropped them. ``to_array_backed`` keeps
        the objects, so this only fires for a store assembled some other way (e.g.
        ``ada.fem.concat``, which merges blocks and does not carry the objects across).
        """
        if self._warned_lost_specials:
            return
        self._warned_lost_specials = True

        from ada.fem.shapes.definitions import ConnectorTypes, MassTypes, SpringTypes

        backed = {e.id for e in self._iter_specials()}
        lost = 0
        for ctype, blk in self._store.blocks.items():
            if not isinstance(ctype, (MassTypes, SpringTypes, ConnectorTypes)):
                continue
            lost += sum(1 for eid in blk.el_ids.tolist() if int(eid) not in backed)
        if lost:
            logger.warning(
                f"{lost} packed mass/spring/connector element(s) carry no Mass/Spring/Connector object: "
                "their values (mass, stiffness, connector section) are not held by the array store and "
                "will be missing from any exported deck."
            )

    @property
    def masses(self):
        from ada.fem.elements import Mass

        self._warn_on_unbacked_special_blocks()
        return LazyElemSeq(lambda: (e for e in self._iter_specials() if isinstance(e, Mass)))

    @property
    def connectors(self):
        from ada.fem.elements import Connector

        return LazyElemSeq(lambda: (e for e in self._iter_specials() if isinstance(e, Connector)))

    @property
    def elements(self) -> list:
        return list(self)

    @property
    def idmap(self) -> dict:
        return {e.id: e for e in self}

    def group_by_type(self):
        for ctype, blk in self._store.blocks.items():
            yield ctype, [self._store.elem_proxy(ctype, r) for r in range(len(blk))]

    # type-filtered views (mirror the object-model properties): re-iterable +
    # cheap len() from block sizes (no materialisation).
    def _of_group(self, group_cls):
        for ctype, blk in self._store.blocks.items():
            if isinstance(ctype, group_cls):
                for r in range(len(blk)):
                    yield self._store.elem_proxy(ctype, r)

    def _count_group(self, group_cls) -> int:
        return sum(len(blk) for ctype, blk in self._store.blocks.items() if isinstance(ctype, group_cls))

    def _group_view(self, group_cls) -> LazyElemSeq:
        return LazyElemSeq(lambda: self._of_group(group_cls), counter=lambda: self._count_group(group_cls))

    @property
    def shell(self):
        from ada.fem.elements import Elem

        return self._group_view(Elem.EL_TYPES.SHELL_SHAPES)

    @property
    def lines(self):
        from ada.fem.elements import Elem

        return self._group_view(Elem.EL_TYPES.LINE_SHAPES)

    @property
    def solids(self):
        from ada.fem.elements import Elem

        return self._group_view(Elem.EL_TYPES.SOLID_SHAPES)

    @property
    def springs(self):
        from ada.fem.shapes.definitions import ElemShapeTypes

        return LazyElemSeq(lambda: (e for e in self._iter_specials() if e.type in ElemShapeTypes.springs))

    @property
    def stru_elements(self):
        from ada.fem.elements import Connector, Mass, Spring

        # Mirror FemElements.stru_elements: the special elements in _overflow are not
        # structural. This used to hand back everything, masses included, which only
        # went unnoticed because nothing filtered by it on the array path yet.
        not_strus = (Mass, Connector, Spring)
        return LazyElemSeq(lambda: (e for e in self if isinstance(e, not_strus) is False))

    def renumber(self, start_id=1, renumber_map: dict = None):
        from ada.fem.elements import Mass

        # A retained special element (``_packed_specials``) carries its own ``_el_id``
        # beside the packed row the store is about to renumber. Note where each one sits
        # first, then copy the row's new id back onto the object, so the two
        # representations of one element cannot drift apart.
        packed_locs = []
        for el in self._packed_specials:
            try:
                ctype, row = self._store.elem_loc(el.id)
            except ValueError:
                continue
            packed_locs.append((el, ctype, row))

        self._store.renumber_elems(start_id=start_id, renumber_map=renumber_map)

        for el, ctype, row in packed_locs:
            el._el_id = int(self._store.blocks[ctype].el_ids[row])
        # The store only knows about its blocks, so the overflow elements (Mass, Spring,
        # Connector) have to be renumbered alongside them or they keep pre-renumber ids
        # while every set member around them moves on. Mass is excluded for the same
        # reason FemElements._renumber_from_map excludes it: mass ids follow the node
        # renumbering, not the element map.
        overflow = [el for el in self._overflow if isinstance(el, Mass) is False]
        if renumber_map is not None:
            for el in overflow:
                # An id absent from the map keeps what it has, matching
                # _remap_id_backed_sets' pass-through for already-final ids.
                el._el_id = renumber_map.get(el.id, el.id)
            return

        nxt = self.max_el_id + 1
        for el in sorted(overflow, key=lambda e: e.id):
            el._el_id = nxt
            nxt += 1

    def to_elem_blocks(self):
        from ada.fem.results.common import ElementBlock, ElementInfo, FEATypes

        out = []
        for ctype, blk in self._store.blocks.items():
            info = ElementInfo(ctype, FEATypes.GMSH, None)
            # Zero-copy: hand over the store's index connectivity + ids directly
            # (node_refs are already 0-based row indices into FemNodes.coords).
            out.append(ElementBlock(info, blk.conn, blk.el_ids, node_refs_are_indices=True))
        return out


def _rebind_special_to_store(elem, store: MeshArrays) -> None:
    """Re-point a retained special element's node references at the substrate.

    It was built against the object ``Node`` instances this conversion drops. Leaving them
    in place would defeat the conversion twice over: a ``Node`` holds the elements that
    reference it in ``Node.refs``, so one retained mass transitively pins the *entire*
    object mesh in memory; and the coordinates would freeze, because ``ArrayNodes.move`` /
    ``rounding_node_points`` write the arrays, not those objects. Anything that is not a
    node the store knows (a bare int id, a node that was not packed) is left untouched.
    """

    def resolved(node):
        nid = getattr(node, "id", None)
        if nid is None or not store.has_node(int(nid)):
            return node
        return store.node_proxy_by_id(int(nid))

    # Mass keeps its nodes on ``_members`` (``Mass.fem_set``'s setter fills that in and
    # leaves ``_nodes`` alone); Spring/Connector additionally cache their ends on _n1/_n2.
    for attr in ("_nodes", "_members"):
        seq = getattr(elem, attr, None)
        if seq:
            setattr(elem, attr, [resolved(n) for n in seq])
    for attr in ("_n1", "_n2"):
        node = getattr(elem, attr, None)
        if node is not None:
            setattr(elem, attr, resolved(node))


def to_array_backed(fem):
    """Swap a FEM's object-model ``nodes``/``elements`` for substrate-backed facades
    sharing one ``MeshArrays``. The proxies are transient, so after this the mesh is
    held as packed arrays. Returns the same FEM for chaining.

    Any FemSet that still holds object members is flipped to id-backed first, so the
    object Node/Elem become unreferenced and are reclaimed.

    The special elements (Mass / Spring / Connector) are kept as objects. They are packed
    into blocks like anything else — ``MeshArrays.from_fem`` groups by ``Elem.type`` — but
    a block stores connectivity and ids only, so a mass value, a spring stiffness or a
    connector section has nowhere to go and was being dropped here. Nothing complained:
    ``fem.elements.masses`` simply went empty and the Sesam writer emitted no BNMASS at
    all for the converted model. The rows stay in their blocks (every consumer that walks
    the store is unchanged); the objects are handed to
    :attr:`ArrayElements._packed_specials` so ``masses``/``springs``/``connectors`` still
    find them."""
    from ada.fem.elements import Connector, Mass, Spring

    # Flip sets to id-backed BEFORE building the store / dropping object containers,
    # otherwise the sets keep the object mesh alive.
    for fs in list(fem.sets):
        fs.to_id_backed()

    # Collect before the object containers go — these are the only copies of their values.
    specials = [el for el in fem.elements if isinstance(el, (Mass, Connector, Spring))]

    store = MeshArrays.from_fem(fem)
    fem.nodes = ArrayNodes(store, parent=fem)
    elements = ArrayElements(store, fem_obj=fem)
    for el in specials:
        _rebind_special_to_store(el, store)
        try:
            store.elem_loc(el.id)
        except ValueError:
            # Not packed (ragged group, or already an overflow element on a re-conversion).
            elements._overflow.append(el)
        else:
            elements._packed_specials.append(el)
    fem.elements = elements
    return fem
