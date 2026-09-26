from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterator, List, Tuple, Union

import numpy as np

from ada.api.nodes import Node

from .common import FemBase
from .elements import Elem
from .sets import FemSet

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem.shapes.definitions import LineShapes, ShellShapes


class SurfTypes:
    ELEMENT = "ELEMENT"
    NODE = "NODE"

    all = [ELEMENT, NODE]


@dataclass
class ElemSurface:
    fem_set: FemSet
    side_index: int


class Surface(FemBase):
    """
    Documentation

        https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-surface.htm#simakey-r-surface__simakey-r-surface-s-datadesc5


    Parameters.

    :param name: Unique name of surface
    :param surf_type: Type of surface
    :param fem_set:
    :param weight_factor:
    :param id_refs: Explicitly defined by list of tuple [(elid/nid,spos), ..]
    :param parent:
    :param metadata:
    """

    TYPES = SurfTypes

    def __init__(
        self,
        name,
        surf_type,
        fem_set: Union[FemSet, List[FemSet]],
        weight_factor=None,
        el_face_index: Union[int, List[int]] = None,
        id_refs=None,
        parent: "FEM" = None,
        metadata=None,
    ):
        super().__init__(name, metadata, parent)

        self._type = surf_type.upper()

        if self.type not in SurfTypes.all:
            raise ValueError(f'Surface type "{self.type}" is currently not supported\\implemented. Valid types are')

        self._fem_set = fem_set
        if isinstance(fem_set, list):
            if not isinstance(el_face_index, list):
                raise ValueError("You cannot define a list of FemSets and not also include a List of el_face_indices")

        self._weight_factor = weight_factor
        self._el_face_index = el_face_index
        self._id_refs = id_refs
        self._refs = []
        if isinstance(fem_set, FemSet):
            fem_set.refs.append(self)

    @property
    def type(self):
        return self._type

    @property
    def fem_set(self) -> Union[FemSet, List[FemSet]]:
        return self._fem_set

    @fem_set.setter
    def fem_set(self, value: Union[FemSet, List[FemSet]]):
        self._fem_set = value

    @property
    def weight_factor(self):
        return self._weight_factor

    @property
    def el_face_index(self) -> Union[int, List[int]]:
        return self._el_face_index

    @property
    def id_refs(self):
        return self._id_refs

    @property
    def refs(self):
        return self._refs


def create_surface_from_nodes(surface_name: str, nodes: List[Node], fem: "FEM", shell_positive=True) -> Surface:
    from ada.fem.elements import find_element_type_from_list
    from ada.fem.shapes import ElemType

    all_el = [el for n in nodes for el in filter(lambda x: type(x) is Elem, n.refs)]
    el_type = find_element_type_from_list(all_el)

    surf_map = {
        ElemType.SOLID: get_surface_from_nodes_on_solid_elements,
        ElemType.SHELL: get_surface_from_nodes_on_shell_elements,
    }
    surf_writer = surf_map.get(el_type, None)

    if surf_writer is None:
        raise NotImplementedError(f'Currently Surface writing on element type "{el_type}" is not supported')

    return surf_writer(surface_name, all_el, nodes, fem, shell_positive)


def get_surface_from_nodes_on_solid_elements(
    surface_name: str, all_el: List[Elem], nodes: List[Node], fem: "FEM", shell_positive: bool
) -> Surface:
    """A solid ``Surface`` covering the faces of ``all_el`` that lie in ``nodes``.

    The face number written into the set name and ``el_face_index`` is an **Abaqus** face
    number, which is what every consumer of this surface expects -- including
    :func:`side_node_indices`, which resolves it back into nodes. It used to come from
    ``elem_has_parallel_face``, i.e. an index into the *visualisation* face sequence.
    Those two agree for tetrahedra and hexahedra by coincidence, but not for a wedge: the
    viz table triangulates the three quad faces, so it has six entries for a five-faced
    element. A wedge could therefore come out labelled with the wrong face, or with an
    "S6" that does not exist on that element at all.
    """
    elements = []
    face_seq_indices = {}
    for el in all_el:
        paralell_face_index = solid_abaqus_face_index(el, nodes)
        if paralell_face_index is None:
            continue

        if el not in elements:
            face_seq_indices[el] = paralell_face_index
            elements.append(el)

    fsets = []
    fset_el_face_indices = []
    for el, el_face_index in face_seq_indices.items():
        side_name = f"S{el_face_index + 1}"
        fs = FemSet(f"_{surface_name}_{el.id}_{side_name}", [el])
        if fs.name in fem.sets.elements.keys():
            fs_elem_1 = fem.sets.elements[fs.name]
            fs_elem_1.add_members([el])
        else:
            fs_elem_1 = fem.add_set(fs)
        fsets.append(fs_elem_1)
        fset_el_face_indices.append(el_face_index)

    return Surface(surface_name, Surface.TYPES.ELEMENT, fsets, el_face_index=fset_el_face_indices)


def get_surface_from_nodes_on_shell_elements(
    surface_name: str, all_el: List[Elem], nodes: List[Node], fem: "FEM", shell_positive: bool
) -> Surface:
    elements = []
    for el in all_el:
        if elem_has_parallel_face(el, nodes) is None:
            continue
        elements.append(el)

    side_name = 1 if shell_positive is True else -1
    fs = fem.add_set(FemSet(f"_{surface_name}_{side_name}", elements))

    return Surface(surface_name, Surface.TYPES.ELEMENT, fs, el_face_index=side_name)


def solid_abaqus_face_index(el: Elem, nodes: List[Node]) -> Union[int, None]:
    """0-based Abaqus face number of the face of solid ``el`` whose corner nodes all lie
    in ``nodes``, or ``None`` if no face does.

    Matching is on corner nodes only, which is what the viz-table version did and what
    keeps this working for a caller that collects corner nodes without the mid-side ones.
    The number returned is the Abaqus one, so ``S{index + 1}`` names a face that really
    exists on that element -- see :data:`ada.fem.shapes.solids.solid_abaqus_face_corners`.
    """
    from ada.fem.shapes.solids import solid_abaqus_face_corners

    faces = solid_abaqus_face_corners.get(el.type)
    if faces is None:
        raise ValueError(
            f"No Abaqus face numbering is defined for element type {el.type}. A surface on it "
            f"cannot be given a face number; see solid_abaqus_faces for the types that are covered."
        )
    for i, slots in enumerate(faces):
        if all(el.nodes[s] in nodes for s in slots):
            return i
    return None


def elem_has_parallel_face(el: Elem, nodes: List[Node]):
    """Whether any *visualisation* face of ``el`` lies wholly in ``nodes``, as its index.

    Used by the shell path purely as a predicate -- the index is discarded there, since a
    shell surface is named by side (``SPOS``/``SNEG``), not by face number. For solids use
    :func:`solid_abaqus_face_index`, whose index is an Abaqus face number.
    """
    for i, nid_refs in enumerate(el.shape.faces_seq):
        all_face_nodes_in_plane = True
        for nid in nid_refs:
            no = el.nodes[nid]
            if no not in nodes:
                all_face_nodes_in_plane = False
                break
        if all_face_nodes_in_plane is True:
            return i
    return None


#: Sides that cover the whole element rather than one of its faces. A shell's
#: ``SPOS`` / ``SNEG`` are its two *faces*: every node of the element lies on
#: each of them, so no filtering applies. The reader spells the same two as
#: ``+1`` / ``-1`` when it has a shell elset in hand.
WHOLE_ELEMENT_SIDES = frozenset({"SPOS", "SNEG"})


def is_blank_side(side) -> bool:
    """Whether ``side`` is an element-surface entry that named **no face identifier**.

    That is a different thing from ``None``. ``None`` is "this region has no side concept
    at all" -- a plain ``FemSet``, or a ``NODE``-type surface whose members are nodes
    already -- and means the members, whole. A blank *string* is an element surface row
    the deck wrote as ``_LinDepPlSurf_,``: the deck did have somewhere to put a face
    identifier and left it empty, which in Abaqus means something specific per element
    family (see :func:`free_face_indices` and :func:`surface_facets`).
    """
    return isinstance(side, str) and side.strip() == ""


def free_face_indices(elements: List[Elem]) -> Union[List[List[int]], None]:
    """Which Abaqus faces of each of ``elements`` are **free**, i.e. on no other element.

    ``[[i, ...], ...]`` aligned with ``elements``: entry *k* holds the 0-based Abaqus face
    numbers of ``elements[k]`` that no other continuum element of the mesh shares, so face
    ``S{i + 1}`` is on the free surface. An element buried in the interior of the body gets
    an empty list. ``None`` means the free faces **cannot be determined** -- the caller must
    report that rather than guess (see :func:`_group_facets`).

    This is the rule Abaqus applies to an element-based surface entry that names no face
    identifier for a continuum element. Two faces are the same face when their **corner**
    node ids match; mid-side nodes are not consulted, so a first-order element meeting a
    second-order one across a shared face still counts as shared, and two coincident but
    unmerged nodes correctly do *not* make a face shared.

    **Where the adjacency comes from, and what it costs.** Not from a mesh-wide face table:
    a face of a listed element can only be shared by an element that uses its nodes, and
    every ``Node`` already back-references its elements (``Elem.__init__`` calls
    ``node.add_obj_to_refs``). So the candidate neighbourhood is read straight off the
    listed elements' nodes and the face-count map is built over that alone. Measured on the
    user's 460,738-element deck, for the 132-element side-less entry that motivated this:
    **23 ms** for the neighbourhood walk against **8.6 s** to hash all 946,021 solid faces
    of the mesh. Both agree on all 132 free faces. The cost is therefore proportional to the
    surface's own neighbourhood, not to the model, and nothing is cached -- at 23 ms a cache
    would only add a way to be stale after a mesh edit.

    ``None`` is returned, rather than a guess, when either half of that is missing:

    * the back-references do not even contain the elements passed in (a ``FEM`` rebuilt
      from a pickle drops ``Node._refs``), so absent neighbours cannot be distinguished
      from absent bookkeeping;
    * some element of the neighbourhood has no Abaqus face numbering in
      :data:`ada.fem.shapes.solids.solid_abaqus_face_corners` (a pyramid, a C3D27), so its
      faces cannot be matched and a face of a listed element next to it would read as free
      when it is not.
    """
    from ada.fem.shapes.definitions import SolidShapes
    from ada.fem.shapes.solids import solid_abaqus_face_corners

    neighbourhood = {}
    for el in elements:
        for n in el.nodes:
            for ref in n.refs:
                if isinstance(getattr(ref, "type", None), SolidShapes):
                    neighbourhood[id(ref)] = ref

    if any(id(el) not in neighbourhood for el in elements):
        return None  # Node._refs is not carrying the elements it should

    face_count = {}
    for el in neighbourhood.values():
        corners = solid_abaqus_face_corners.get(el.type)
        if corners is None:
            return None  # a neighbour whose faces cannot be spelled out
        for slots in corners:
            key = frozenset(el.nodes[s].id for s in slots)
            face_count[key] = face_count.get(key, 0) + 1

    free = []
    for el in elements:
        corners = solid_abaqus_face_corners[el.type]
        free.append([i for i, slots in enumerate(corners) if face_count[frozenset(el.nodes[s].id for s in slots)] == 1])
    return free


def side_node_indices(el_type, side) -> Union[tuple, None]:
    """Local node slots of ``el_type`` lying on surface side ``side``.

    ``None`` means "this cannot be narrowed from a type and a side alone". Two cases reach
    it, and they are **not** the same thing:

    * the side is a shell face (``SPOS`` / ``SNEG``, or the reader's ``+1`` / ``-1``), which
      every node of the element genuinely lies on -- the whole element is the answer;
    * the side is blank, i.e. the entry named no face identifier. For a shell that is again
      the whole element, but for a **continuum** element Abaqus reads it as the element's
      *free* faces, which depends on the element's neighbours and so cannot be answered
      here. :func:`surface_facets` and :func:`surface_nodes` do answer it, via
      :func:`free_face_indices`; a caller that resolves sides through this function alone
      gets the whole element and therefore a superset for such an entry.

    ``side`` is taken as the deck wrote it: ``"S3"`` / ``"E2"`` / ``"SPOS"``, or the
    integer the Abaqus reader normalises a single-elset surface to (0-based face
    index for a solid, ``+1`` / ``-1`` for a shell).

    Raises on a side this has no map for, naming the element type and the side.
    Falling back to every node of the element is exactly the bug this exists to
    fix: a surface would silently cover the interior of its elements and, for a
    tie or coupling, roughly twice the nodes the deck asked for.
    """
    from ada.fem.shapes.definitions import ShellShapes, SolidShapes
    from ada.fem.shapes.shells import shell_abaqus_edges
    from ada.fem.shapes.solids import solid_abaqus_faces

    if side is None:
        return None

    if isinstance(side, str):
        spec = side.strip().upper()
        if spec == "" or spec in WHOLE_ELEMENT_SIDES:
            return None
        table, index = None, None
        if len(spec) > 1 and spec[1:].isdigit():
            index = int(spec[1:]) - 1
            if spec[0] == "S":
                table = solid_abaqus_faces.get(el_type)
            elif spec[0] == "E":
                table = shell_abaqus_edges.get(el_type)
    else:
        # The reader's normalised form. For a shell it is the +1/-1 SPOS/SNEG flag,
        # not a face index; for a solid it is already 0-based.
        if isinstance(el_type, ShellShapes):
            return None
        index = int(side)
        table = solid_abaqus_faces.get(el_type) if isinstance(el_type, SolidShapes) else None

    if table is None or index is None or not 0 <= index < len(table):
        raise ValueError(
            f'No Abaqus face/edge map for element type "{el_type}" side "{side}". '
            "Add the element's face numbering to ada.fem.shapes (solid_abaqus_faces / "
            "shell_abaqus_edges) rather than letting the surface fall back to every "
            "node of every element."
        )

    return table[index]


def _region_groups(region: Union["Surface", FemSet]):
    """The region as ``(members, side)`` groups, one per set the region names.

    Grouping rather than flattening keeps the side alongside the members it applies
    to, and lets the caller resolve the side once per element *type* per group
    instead of once per element.
    """
    if isinstance(region, FemSet):
        # A set names no side -- it is the members themselves, whole.
        return [(region.members, None)]

    groups = []
    fem_set = region.fem_set
    fem_sets = fem_set if isinstance(fem_set, list) else [fem_set]
    el_face_index = region.el_face_index
    if isinstance(el_face_index, list):
        if len(el_face_index) != len(fem_sets):
            raise ValueError(
                f'Surface "{region.name}" has {len(fem_sets)} FemSet(s) but {len(el_face_index)} el_face_index entries'
            )
        sides = el_face_index
    else:
        sides = [el_face_index] * len(fem_sets)

    nodal = region.type == SurfTypes.NODE
    for fs, side in zip(fem_sets, sides):
        if fs is not None:
            groups.append((fs.members, None if nodal else side))

    id_refs = region.id_refs or []
    if id_refs and region.parent is None:
        raise ValueError(f'Surface "{region.name}" references sets by name but has no parent FEM')

    # A multi-row surface (``_LIP_UNDERSIDE_S3, S3`` / ``_LIP_UNDERSIDE_S1, S1`` ...)
    # is held entirely in ``id_refs``: the Abaqus reader leaves ``fem_set`` and
    # ``el_face_index`` as None there and keeps each row's side label in the second
    # slot of the row. For a NODE surface that slot is a weight factor instead.
    for ref, ref_side in ((r[0], (None if nodal else r[1] if len(r) > 1 else None)) for r in id_refs):
        if isinstance(ref, str):
            sets = region.parent.sets
            fs = sets.get_nset_from_name(ref) if nodal else sets.get_elset_from_name(ref)
            groups.append((fs.members, ref_side))
        else:
            member = region.parent.nodes.from_id(ref) if nodal else region.parent.elements.from_id(ref)
            groups.append(([member], ref_side))

    return groups


@dataclass(frozen=True)
class SurfaceFacet:
    """One facet of an element-based surface: a solid face, a shell face, or a shell edge.

    A facet is the unit a surface is actually made of, and the unit a caller needs when it
    has to do geometry *on* the surface rather than merely collect its nodes -- project a
    point onto it and evaluate the facet's shape functions at the projection, say. That is
    why ``nodes`` is an ordered tuple and why ``shape`` is carried explicitly: node count
    alone does not identify a facet (three nodes is a TRI3 solid face or a LINE3 shell edge,
    six is a TRI6 face) and the shape functions differ.

    :param element: the element the facet belongs to. Kept so a caller can report *which*
        element a facet came from, and reach its type, id and parent.
    :param shape: the facet's own topology -- a :class:`~ada.fem.shapes.definitions.ShellShapes`
        member for a two-dimensional facet (a solid face, or a shell element's face), a
        :class:`~ada.fem.shapes.definitions.LineShapes` member for a shell edge. Never the
        *solid* shape of ``element``.
    :param nodes: the facet's nodes **in Abaqus order** -- corners in face-winding order
        first, then the mid-side node of each of those edges in the same order. Ordering is
        what makes shape-function evaluation possible, so it is part of the contract, not an
        accident of iteration.
    :param node_indices: the same nodes as local slots into ``element.nodes``, in the same
        order. Handy for a caller that wants to say "face S3" or to index parallel arrays.
    :param side: the surface side this facet was resolved from, exactly as the region carried
        it -- ``"S3"``, ``"E2"``, ``"SPOS"``, the reader's integer, ``""`` for an entry that
        named no face identifier, or ``None`` for a region with no side concept. Reported
        rather than interpreted, so a caller can explain itself.
    """

    element: Elem
    shape: Union["ShellShapes", "LineShapes"]
    nodes: Tuple[Node, ...]
    node_indices: Tuple[int, ...]
    side: Union[str, int, None]

    @property
    def node_ids(self) -> Tuple[int, ...]:
        """The facet's node ids, in the same order as :attr:`nodes`."""
        return tuple(n.id for n in self.nodes)

    @property
    def points(self) -> np.ndarray:
        """``(len(nodes), 3)`` of the facet's node coordinates, in the same order."""
        return np.array([n.p for n in self.nodes], dtype=float)


#: ``keyword`` the free-face findings are filed under, and the ``stage`` they belong to.
#: The rule being applied is Abaqus' reading of a ``*SURFACE`` entry, so the finding is
#: about the deck even though it is resolved lazily, while something else is writing.
_SURFACE_STAGE = "abaqus reader"
_SURFACE_KEYWORD = "*SURFACE"


def _group_facets(region_name: str, members, side) -> Iterator[Tuple[Union[SurfaceFacet, None], Union[Elem, Node]]]:
    """The facets of one ``(members, side)`` region group, plus the members with none.

    Yields ``(facet_or_None, member)`` pairs so that the two callers can share exactly one
    reading of the side -- :func:`surface_facets` keeps the facets and :func:`surface_nodes`
    takes their nodes, falling back to the whole member where the facet is ``None``. Having
    them disagree about what a surface covers is the failure this shape is here to prevent.

    ``facet is None`` on an element member means "no facet could be formed, and the whole
    element is what the surface covers": a line element, a continuum element the region names
    with no side at all, or one whose free faces could not be determined. For a ``Node`` member
    it simply means a node is not a facet.

    The side is read as follows:

    * a named side (``"S3"``, ``"E2"``, an integer) -> that one face or edge;
    * ``SPOS`` / ``SNEG`` -> the shell's own face, every node of it;
    * **blank** (an element-surface entry that named no face identifier):
      a shell keeps the whole element -- a blank side means both faces and every node lies
      on each -- while a **continuum** element contributes its *free* faces, one facet per
      free face, and nothing at all when it is fully interior. That is Abaqus' rule for such
      an entry, and reading it as the whole element instead pulls in nodes that lie on faces
      interior to the body and are on no surface;
    * ``None`` (a plain ``FemSet``, a ``NODE`` surface) -> the members whole. No free-face
      reading here: ``None`` is adapy's own "a set, not a surface", which it has always taken
      as whole membership, and a set is not an Abaqus surface entry to apply the rule to.
    """
    from ada.fem.formats import conversion_report
    from ada.fem.shapes.definitions import ShellShapes, SolidShapes
    from ada.fem.shapes.shells import shell_abaqus_edge_shapes
    from ada.fem.shapes.solids import solid_abaqus_face_shapes, solid_abaqus_faces

    blank = is_blank_side(side)
    free_faces = None
    if blank:
        # One neighbourhood walk for the whole group, not one per element: the candidate
        # elements and the face-count map are shared by every element in it.
        solids = [m for m in members if isinstance(getattr(m, "type", None), SolidShapes)]
        if solids:
            per_element = free_face_indices(solids)
            if per_element is None:
                conversion_report.current().suspect(
                    _SURFACE_STAGE,
                    _SURFACE_KEYWORD,
                    region_name,
                    f'surface "{region_name}" has an element entry naming no face identifier, whose free '
                    "faces cannot be determined, so every node of those continuum elements is taken as "
                    "being on the surface -- which includes nodes on faces interior to the body",
                    n_elements=len(solids),
                    element_types=sorted({str(el.type) for el in solids}),
                )
            else:
                free_faces = {id(el): faces for el, faces in zip(solids, per_element)}

    slots_by_type = {}
    for m in members:
        m_nodes = getattr(m, "nodes", None)
        if m_nodes is None:  # a Node, from a nodal set or an explicit node id
            yield None, m
            continue

        el_type = m.type
        if blank or side is None:
            if isinstance(el_type, ShellShapes):
                # A shell's face is the element: all of its nodes, in element order.
                yield SurfaceFacet(m, el_type, tuple(m_nodes), tuple(range(len(m_nodes))), side), m
            elif free_faces is not None and id(m) in free_faces:
                faces = solid_abaqus_faces[el_type]
                shapes = solid_abaqus_face_shapes[el_type]
                for i in free_faces[id(m)]:
                    slots = tuple(faces[i])
                    yield SurfaceFacet(m, shapes[i], tuple(m_nodes[s] for s in slots), slots, side), m
            else:
                yield None, m
            continue

        try:
            slots = slots_by_type[el_type]
        except KeyError:
            slots = slots_by_type[el_type] = side_node_indices(el_type, side)
        if slots is None:
            # SPOS / SNEG, or the reader's +1 / -1: the shell's own face.
            shape = el_type if isinstance(el_type, ShellShapes) else None
            if shape is None:
                yield None, m
            else:
                yield SurfaceFacet(m, shape, tuple(m_nodes), tuple(range(len(m_nodes))), side), m
            continue

        slots = tuple(slots)
        if isinstance(el_type, ShellShapes):
            shape = shell_abaqus_edge_shapes[el_type][_side_index(side)]
        else:
            shape = solid_abaqus_face_shapes[el_type][_side_index(side)]
        yield SurfaceFacet(m, shape, tuple(m_nodes[s] for s in slots), slots, side), m


def _side_index(side) -> int:
    """0-based face / edge number of a named side -- ``"S3"`` -> 2, ``"E2"`` -> 1, ``2`` -> 2.

    Only reached for a side :func:`side_node_indices` has already resolved to a slot tuple,
    so the spelling is known to be one of these two.
    """
    if isinstance(side, str):
        return int(side.strip().upper()[1:]) - 1
    return int(side)


def surface_facets(region: Union[Surface, FemSet]) -> List[SurfaceFacet]:
    """The facets of an element-based surface (or set), in region order.

    One :class:`SurfaceFacet` per solid face, shell face or shell edge the region covers, in
    the order the region lists its members and, within a member, in Abaqus face / edge order.
    Members that carry no facet are simply absent: a node set has no facets, and neither has a
    continuum element that a region names without a side (see :func:`_group_facets` for the
    full reading of a side, and for the ``suspect`` finding raised when an entry that names no
    face identifier has free faces that cannot be determined).

    Use this rather than :func:`surface_nodes` when the geometry of the surface matters and
    not merely which nodes are on it -- projecting a point onto the surface, integrating over
    it, or interpolating between a facet's nodes. The facets and the nodes are resolved by the
    same code, so the two cannot drift apart.
    """
    name = _region_name(region)
    return [
        facet
        for members, side in _region_groups(region)
        for facet, _ in _group_facets(name, members, side)
        if facet is not None
    ]


def _region_name(region: Union[Surface, FemSet]) -> str:
    return getattr(region, "name", "") or ""


def surface_nodes(region: Union[Surface, FemSet]) -> List[Node]:
    """Unique nodes covered by a surface (or a plain set), in first-seen order.

    A ``Surface`` names its region through one or more ``FemSet`` objects, or -- when a
    deck listed several sets under a single surface -- through ``id_refs`` entries
    naming those sets or element / node ids outright. Callers that only need the nodes
    shouldn't have to know which of the three they got.

    For an element-based surface the side matters: ``_LIP_UNDERSIDE_S3, S3`` covers the
    S3 face of each of those tetrahedra, not all ten of their nodes, and an entry that
    names **no** face identifier covers a continuum element's *free* faces -- not every
    node of it, which would reach into the interior of the body. The nodes come back
    mid-side nodes and all. A ``FemSet`` handed over directly names no side, and a
    NODE-type surface's members are nodes already, so both keep their whole-membership
    behaviour.

    Shares its reading of the surface with :func:`surface_facets`, node for node.
    """
    nodes = {}
    name = _region_name(region)
    for members, side in _region_groups(region):
        for facet, member in _group_facets(name, members, side):
            if facet is None:
                m_nodes = getattr(member, "nodes", None)
                if m_nodes is None:  # a Node
                    nodes.setdefault(member.id, member)
                    continue
                for n in m_nodes:
                    nodes.setdefault(n.id, n)
                continue
            for n in facet.nodes:
                nodes.setdefault(n.id, n)
    return list(nodes.values())
