from __future__ import annotations

import logging
from typing import Union

import numpy as np

# The element names are based on the naming scheme by meshio


class LineShapes:
    LINE = "LINE"
    LINE3 = "LINE3"

    all = [LINE, LINE3]


class ShellShapes:
    TRI = "TRIANGLE"
    TRI6 = "TRIANGLE6"
    TRI7 = "TRIANGLE7"
    QUAD = "QUAD"
    QUAD8 = "QUAD8"
    QUAD9 = "QUAD9"

    all = [TRI, TRI7, TRI6, QUAD, QUAD8, QUAD9]


class ConnectorShapes:
    CONNECTOR = "CONNECTOR"
    all = [CONNECTOR]


class SolidShapes:
    HEX8 = "HEXAHEDRON"
    HEX20 = "HEXAHEDRON20"
    HEX27 = "HEXAHEDRON27"
    TETRA = "TETRA"
    TETRA10 = "TETRA10"
    PYRAMID5 = "PYRAMID5"
    PYRAMID13 = "PYRAMID13"
    WEDGE = "WEDGE"
    WEDGE15 = "WEDGE15"

    all = [HEX8, HEX20, HEX27, TETRA10, TETRA, WEDGE, WEDGE15, PYRAMID5, PYRAMID13]


class MassShapes:
    MASS = "MASS"
    ROTARYI = "ROTARYI"

    all = [MASS, ROTARYI]


class PointShapes:
    MASS = MassShapes.MASS
    ROTARYI = MassShapes.ROTARYI
    SPRING1 = "SPRING1"

    all = [MASS, ROTARYI, SPRING1]


class ElemType:
    SHELL = "SHELL"
    SOLID = "SOLID"
    LINE = "LINE"

    LINE_SHAPES = LineShapes
    SHELL_SHAPES = ShellShapes
    SOLID_SHAPES = SolidShapes
    POINT_SHAPES = PointShapes
    MASS_SHAPES = MassShapes
    CONNECTOR_SHAPES = ConnectorShapes

    all = [SHELL, SOLID, LINE]


class ElemShapeTypes:
    shell = ShellShapes
    solids = SolidShapes
    lines = LineShapes
    spring1n = ["SPRING1"]
    spring2n = ["SPRING2"]
    springs = spring1n + spring2n
    masses = ["MASS", "ROTARYI"]
    connectors = ["CONNECTOR", "CONN3D2"]
    other2n = connectors
    other = other2n


class ElemShape:
    TYPES = ElemShapeTypes

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
        if self.type in ElemShapeTypes.solids.all:
            faces_seq = self.solids_face_seq
        else:
            faces_seq = self.faces_seq

        if faces_seq is None:
            raise ValueError(f"Element type {self.type} is currently not supported for Visualization")

        if self._faces is None:
            self._faces = [self.nodes[e] for ed_seq in faces_seq for e in ed_seq]

        return self._faces

    @property
    def elem_type_group(self):
        if self.type in up(SolidShapes.all):
            return ElemType.SOLID
        elif self.type in up(ShellShapes.all):
            return ElemType.SHELL
        elif self.type in up(LineShapes.all):
            return ElemType.LINE
        else:
            raise ValueError(f'Unrecognized Element Type: "{self.type}"')

    def update(self, el_type=None, nodes=None):
        if el_type is not None:
            self.type = el_type.upper()
            if ElemShape.is_valid_elem(el_type) is False:
                raise ValueError(f'Currently unsupported element type "{el_type}".')

        nodes = self.nodes if nodes is None else nodes
        num_nodes = ElemShape.num_nodes(self.type)
        if len(nodes) != num_nodes:
            raise ValueError(f'Number of passed nodes "{len(nodes)}" does not match expected "{num_nodes}" ')

        self.nodes = nodes
        self._edges = None

    @property
    def edges_seq(self) -> Union[np.ndarray, None]:
        from .lines import line_edges
        from .shells import shell_edges
        from .solids import solid_edges

        edge_map = {ElemType.LINE: line_edges, ElemType.SHELL: shell_edges, ElemType.SOLID: solid_edges}
        generalized_type = self.type
        edges_repo = edge_map[self.elem_type_group]
        if generalized_type not in edges_repo.keys():
            logging.error(f"Element type {self.type} is currently not supported")
            return None

        return edges_repo[generalized_type]

    @property
    def faces_seq(self):
        from .shells import shell_faces
        from .solids import solid_faces

        face_map = {ElemType.LINE: None, ElemType.SHELL: shell_faces, ElemType.SOLID: solid_faces}
        generalized_type = self.type
        faces_repo = face_map[self.elem_type_group]
        if generalized_type not in faces_repo.keys():
            raise ValueError(f"Element type {self.type} is currently not supported for Visualization")

        return faces_repo[generalized_type]

    @property
    def spring_edges(self):
        if self.type not in ElemShapeTypes.springs:
            return None
        springs = dict(SPRING2=[[0, 1]])
        return springs[self.type]

    @property
    def solids_face_seq(self):
        from .solids import solid_faces

        solid_face_res = solid_faces.get(self.type, None)
        if solid_face_res is None:
            logging.error(f"Element type {self.type} is currently not supported")
            return None
        return solid_face_res

    @staticmethod
    def is_valid_elem(elem_type):
        valid_element_types = (
            ElemType.LINE_SHAPES.all
            + ElemType.SHELL_SHAPES.all
            + ElemType.SOLID_SHAPES.all
            + ElemType.POINT_SHAPES.all
            + ElemType.CONNECTOR_SHAPES.all
        )
        valid_element_types_upper = [x.upper() for x in valid_element_types]
        value = elem_type.upper()
        if value in valid_element_types_upper:
            return True
        else:
            return False

    @staticmethod
    def num_nodes(el_name):
        num_map = {
            1: ElemShapeTypes.masses + ElemShapeTypes.spring1n,
            2: [LineShapes.LINE] + ElemShapeTypes.connectors,
            3: [LineShapes.LINE3, ShellShapes.TRI],
            4: [ShellShapes.QUAD, SolidShapes.TETRA],
            5: [SolidShapes.PYRAMID5],
            6: [ShellShapes.TRI6, SolidShapes.WEDGE],
            8: [SolidShapes.HEX8, ShellShapes.QUAD8],
            10: [SolidShapes.TETRA10],
            15: [SolidShapes.WEDGE15],
            20: [SolidShapes.HEX20],
            27: [SolidShapes.HEX27],
        }
        for num, el_types in num_map.items():
            if el_name in el_types or el_name.lower() in el_types:
                return num

        raise ValueError(f'element type "{el_name}" is not yet supported')

    def __repr__(self):
        return f'{self.__class__.__name__}(Type: {self.type}, NodeIds: "{self.nodes}")'


def get_elem_type_group(el_type):
    el_type = el_type.upper()

    if el_type in up(SolidShapes.all):
        return ElemType.SOLID
    elif el_type in up(ShellShapes.all):
        return ElemType.SHELL
    elif el_type in up(LineShapes.all):
        return ElemType.LINE
    else:
        raise ValueError(f'Unrecognized Element Type: "{el_type}"')


def up(variables):
    return [v.upper() for v in variables]
