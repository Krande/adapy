import logging
from typing import Union

import numpy as np

from .abaqus_line_el import line_edges
from .abaqus_sh_el import shell_edges, shell_faces
from .abaqus_vol_el import volume_edges, volume_faces
from .mesh_types import abaqus_to_meshio_type

# Node numbering of elements is based on GMSH doc here http://gmsh.info/doc/texinfo/gmsh.html#Node-ordering


class ElemType:
    SHELL = "shell"
    SOLID = "solid"
    LINE = "line"


edge_map = {ElemType.LINE: line_edges, ElemType.SHELL: shell_edges, ElemType.SOLID: volume_edges}
face_map = {ElemType.LINE: None, ElemType.SHELL: shell_faces, ElemType.SOLID: volume_faces}


class ElemShapes:
    # 2D elements
    tri = ["S3", "S3R", "R3D3", "S3RS"]
    tri6 = ["STRI65"]
    tri7 = ["S7"]
    quad = ["S4", "S4R", "R3D4"]
    quad8 = ["S8", "S8R"]
    shell = tri + quad + quad8 + tri6 + tri7
    # 3D elements
    cube8 = ["C3D8", "C3D8R", "C3D8H"]
    cube20 = ["C3D20", "C3D20R", "C3D20RH"]
    cube27 = ["C3D27"]
    tetrahedron = ["C3D4"]
    tetrahedron10 = ["C3D10"]
    pyramid5 = ["C3D5", "C3D5H"]
    prism6 = ["C3D6"]
    prism15 = ["C3D15"]
    volume = cube8 + cube20 + tetrahedron10 + tetrahedron + pyramid5 + prism15 + prism6
    # 1D/0D elements
    bm2 = ["B31"]
    bm3 = ["B32"]
    lines = bm2 + bm3
    spring1n = ["SPRING1"]
    spring2n = ["SPRING2"]
    springs = spring1n + spring2n
    masses = ["MASS", "ROTARYI"]
    connectors = ["CONNECTOR", "CONN3D2"]
    other2n = connectors
    other = other2n

    @staticmethod
    def is_valid_elem(elem_type):
        value = elem_type.upper()
        if (
            value
            not in ElemShapes.shell
            + ElemShapes.volume
            + ElemShapes.lines
            + ElemShapes.springs
            + ElemShapes.masses
            + ElemShapes.other
        ):
            return False
        else:
            return True

    @staticmethod
    def num_nodes(el_name):
        num_map = {
            1: ElemShapes.masses + ElemShapes.spring1n,
            2: ElemShapes.bm2 + ElemShapes.spring2n + ElemShapes.other2n,
            3: ElemShapes.tri + ElemShapes.bm3,
            4: ElemShapes.quad + ElemShapes.tetrahedron,
            5: ElemShapes.pyramid5,
            6: ElemShapes.tri6 + ElemShapes.prism6,
            8: ElemShapes.quad8 + ElemShapes.cube8,
            10: ElemShapes.tetrahedron10,
            15: ElemShapes.prism15,
            20: ElemShapes.cube20,
            27: ElemShapes.cube27,
        }
        for num, el_types in num_map.items():
            if el_name in el_types:
                return num

        raise ValueError(f'element type "{el_name}" is not yet supported')

    def __init__(self, el_type, nodes):
        self.type = None
        self.nodes = None
        self._edges = None
        self._faces = None
        self.update(el_type, nodes)

    @property
    def edges(self):
        edges_seq = self.edges_seq
        if edges_seq is None:
            raise ValueError(f"Element type {self.type} is currently not supported for Visualization")
        if self._edges is None:
            self._edges = [self.nodes[e] for ed_seq in edges_seq for e in ed_seq]

        return self._edges

    @property
    def faces(self):
        if self.type in self.volume:
            faces_seq = self.volumes_seq
        else:
            faces_seq = self.faces_seq
        if faces_seq is None:
            raise ValueError(f"Element type {self.type} is currently not supported for Visualization")

        if self._faces is None:
            self._faces = [self.nodes[e] for ed_seq in faces_seq for e in ed_seq]

        return self._faces

    @property
    def elem_type_group(self):
        if self.type in ElemShapes.volume:
            return ElemType.SOLID
        elif self.type in ElemShapes.shell:
            return ElemType.SHELL
        elif self.type in ElemShapes.lines:
            return ElemType.LINE
        else:
            raise ValueError(f'Unrecognized Element Type: "{self.type}"')

    def update(self, el_type=None, nodes=None):
        if el_type is not None:
            self.type = el_type.upper()
            if ElemShapes.is_valid_elem(el_type) is False:
                raise ValueError(f'Currently unsupported element type "{el_type}".')

        nodes = self.nodes if nodes is None else nodes
        num_nodes = ElemShapes.num_nodes(self.type)
        if len(nodes) != num_nodes:
            raise ValueError(f'Number of passed nodes "{len(nodes)}" does not match expected "{num_nodes}" ')

        self.nodes = nodes
        self._edges = None

    @property
    def edges_seq(self) -> Union[np.ndarray, None]:
        generalized_type = abaqus_to_meshio_type.get(self.type, self.type)
        edges_repo = edge_map[self.elem_type_group]
        if generalized_type not in edges_repo.keys():
            logging.error(f"Element type {self.type} is currently not supported")
            return None

        return edges_repo[generalized_type]

    @property
    def faces_seq(self):
        generalized_type = abaqus_to_meshio_type.get(self.type, self.type)
        faces_repo = face_map[self.elem_type_group]
        if generalized_type not in faces_repo.keys():
            raise ValueError(f"Element type {self.type} is currently not supported for Visualization")

        return faces_repo[generalized_type]

    @property
    def spring_edges(self):
        if self.type not in self.springs:
            return None
        springs = dict(SPRING2=[[0, 1]])
        return springs[self.type]

    @property
    def volumes_seq(self):
        generalized_type = abaqus_to_meshio_type.get(self.type, self.type)
        if generalized_type not in volume_faces.keys():
            logging.error(f"Element type {self.type} is currently not supported")
            return None
        return volume_faces[generalized_type]

    def __repr__(self):
        return f'{self.__class__.__name__}(Type: {self.type}, NodeIds: "{self.nodes}")'
