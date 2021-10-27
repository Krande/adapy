from dataclasses import dataclass
from typing import List, Union

from ada.concepts.points import Node

from .common import FemBase
from .elements import Elem
from .sets import FemSet


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
        parent=None,
        metadata=None,
    ):
        """:type parent: ada.FEM"""
        super().__init__(name, metadata, parent)

        self._type = surf_type.upper()

        if self.type not in SurfTypes.all:
            raise ValueError(f'Surface type "{self.type}" is currently not supported\\implemented. Valid types are')

        self._fem_set = fem_set
        if type(fem_set) is list:
            if not type(el_face_index) is list:
                raise ValueError("You cannot define a list of FemSets and not also include a List of el_face_indices")

        self._weight_factor = weight_factor
        self._el_face_index = el_face_index
        self._id_refs = id_refs

    @property
    def type(self):
        return self._type

    @property
    def fem_set(self) -> Union[FemSet, List[FemSet]]:
        return self._fem_set

    @property
    def weight_factor(self):
        return self._weight_factor

    @property
    def el_face_index(self) -> Union[int, List[int]]:
        return self._el_face_index

    @property
    def id_refs(self):
        return self._id_refs


def create_surface_from_nodes(surface_name: str, nodes: List[Node], fem):
    """:type fem: ada.FEM"""
    elements = []
    face_seq_indices = {}
    for n in nodes:
        for el in n.refs:
            if el.id == 4341:
                print("sd")
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
