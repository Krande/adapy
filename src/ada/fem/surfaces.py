from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Union

from ada.api.nodes import Node

from .common import FemBase
from .elements import Elem
from .sets import FemSet

if TYPE_CHECKING:
    from ada import FEM


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


def side_node_indices(el_type, side) -> Union[tuple, None]:
    """Local node slots of ``el_type`` lying on surface side ``side``.

    ``None`` means "the whole element" -- either no side was named at all, or the
    side is a shell face (``SPOS`` / ``SNEG``), which every node of the element
    lies on.

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


def surface_nodes(region: Union[Surface, FemSet]) -> List[Node]:
    """Unique nodes covered by a surface (or a plain set), in first-seen order.

    A ``Surface`` names its region through one or more ``FemSet`` objects, or -- when a
    deck listed several sets under a single surface -- through ``id_refs`` entries
    naming those sets or element / node ids outright. Callers that only need the nodes
    shouldn't have to know which of the three they got.

    For an element-based surface the side matters: ``_LIP_UNDERSIDE_S3, S3`` covers the
    S3 face of each of those tetrahedra, not all ten of their nodes. Only the nodes on
    the named face (or shell edge) come back, mid-side nodes included, per
    :func:`side_node_indices`. A ``FemSet`` handed over directly names no side, and a
    NODE-type surface's members are nodes already, so both keep their whole-membership
    behaviour.
    """
    nodes = {}
    for members, side in _region_groups(region):
        # One small map per group, not per element: a set holds at most a couple of
        # element types, and the surface side is constant across the set.
        per_el_type = {}
        for m in members:
            m_nodes = getattr(m, "nodes", None)
            if m_nodes is None:  # a Node, from a nodal set or an explicit node id
                nodes.setdefault(m.id, m)
                continue
            if side is not None:
                el_type = m.type
                try:
                    slots = per_el_type[el_type]
                except KeyError:
                    slots = per_el_type[el_type] = side_node_indices(el_type, side)
                if slots is not None:
                    for i in slots:
                        n = m_nodes[i]
                        nodes.setdefault(n.id, n)
                    continue
            for n in m_nodes:
                nodes.setdefault(n.id, n)
    return list(nodes.values())
