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
    elements = []
    face_seq_indices = {}
    for el in all_el:
        paralell_face_index = elem_has_parallel_face(el, nodes)
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


def elem_has_parallel_face(el: Elem, nodes: List[Node]):
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
