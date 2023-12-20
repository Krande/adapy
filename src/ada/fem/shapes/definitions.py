from __future__ import annotations

from enum import Enum

import numpy as np

from ada.base.types import GeomRepr
from ada.config import logger


class UnsupportedFeaShapeException(Exception):
    pass


class BaseShapeEnum(Enum):
    @classmethod
    def from_str(cls, value: str, default=None):
        if isinstance(value, cls):
            return value
        key_map = {x.value.lower(): x for x in cls}
        result = key_map.get(value.lower(), default)
        if result is None:
            raise UnsupportedFeaShapeException("Unsupported")

        return result

    @classmethod
    def get_all(cls) -> list:
        return [x for x in cls]

    def __gt__(self, other):
        return self.value > other.value


class LineShapes(BaseShapeEnum):
    LINE = "LINE"
    LINE3 = "LINE3"


class ShellShapes(BaseShapeEnum):
    TRI = "TRIANGLE"
    TRI6 = "TRIANGLE6"
    TRI7 = "TRIANGLE7"
    QUAD = "QUAD"
    QUAD8 = "QUAD8"
    QUAD9 = "QUAD9"


class SolidShapes(BaseShapeEnum):
    HEX8 = "HEXAHEDRON"
    HEX20 = "HEXAHEDRON20"
    HEX27 = "HEXAHEDRON27"
    TETRA = "TETRA"
    TETRA10 = "TETRA10"
    PYRAMID5 = "PYRAMID5"
    PYRAMID13 = "PYRAMID13"
    WEDGE = "WEDGE"
    WEDGE15 = "WEDGE15"


class ConnectorTypes(BaseShapeEnum):
    CONNECTOR = "CONNECTOR"


class MassTypes(BaseShapeEnum):
    MASS = "MASS"
    ROTARYI = "ROTARYI"


class SpringTypes(BaseShapeEnum):
    SPRING1 = "SPRING1"
    SPRING2 = "SPRING2"


class ShapeResolver:
    NUM_MAP = {
        1: MassTypes.get_all() + SpringTypes.get_all(),
        2: [LineShapes.LINE] + ConnectorTypes.get_all(),
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

    @staticmethod
    def get_el_type_from_str(el_type: str) -> LineShapes | ShellShapes | SolidShapes | None:
        for shape in [LineShapes, ShellShapes, SolidShapes, SpringTypes, ConnectorTypes]:
            try:
                result = shape.from_str(el_type)
            except UnsupportedFeaShapeException:
                continue

            return result

        return None

    @staticmethod
    def get_el_nodes_from_type(el_type: LineShapes | ShellShapes | SolidShapes):
        for num, el_types in ShapeResolver.NUM_MAP.items():
            if el_type in el_types:
                return num

        raise ValueError(f'element type "{el_type}" is not yet supported')

    @staticmethod
    def to_geom_repr(el_type):
        if isinstance(el_type, SolidShapes):
            return ElemType.SOLID
        elif isinstance(el_type, ShellShapes):
            return ElemType.SHELL
        elif isinstance(el_type, LineShapes):
            return ElemType.LINE
        else:
            raise ValueError(f'Unrecognized Shape Type: "{el_type}"')


# todo: clean up elem shape types. Mass and connector shapes should be removed (they are either Point or Line shapes) now that they are subclasses of Elem.
class ElemType:
    SHELL = GeomRepr.SHELL
    SOLID = GeomRepr.SOLID
    LINE = GeomRepr.LINE

    LINE_SHAPES = LineShapes
    SHELL_SHAPES = ShellShapes
    SOLID_SHAPES = SolidShapes

    MASS_SHAPES = MassTypes
    CONNECTOR_SHAPES = ConnectorTypes

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

    def get_faces(self):
        from itertools import chain

        def hex_face_to_tris(q):
            return [(q[0], q[1], q[2]), (q[0], q[2], q[3])]

        if isinstance(self.type, SolidShapes):
            faces_seq = self.solids_face_seq
            if self.type in (SolidShapes.HEX8, SolidShapes.HEX20):
                faces_seq = list(chain.from_iterable([hex_face_to_tris(x) for x in faces_seq]))
        else:
            faces_seq = self.faces_seq

        if self._faces is None:
            self._faces = [self.nodes[e] for ed_seq in faces_seq for e in ed_seq]

        return self._faces

    @property
    def faces(self):
        if isinstance(self.type, SolidShapes):
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
        if isinstance(self.type, SolidShapes):
            return ElemType.SOLID
        elif isinstance(self.type, ShellShapes):
            return ElemType.SHELL
        elif isinstance(self.type, (LineShapes, ConnectorTypes)):
            return ElemType.LINE
        elif isinstance(self.type, SpringTypes):
            return ElemType.LINE
        else:
            raise ValueError(f'Unrecognized Element Type: "{self.type}"')

    def update(self, el_type=None, nodes=None):
        if el_type is not None:
            if isinstance(el_type, str):
                el_type = ShapeResolver.get_el_type_from_str(el_type)
            if el_type is None:
                raise ValueError(f'Currently unsupported element type "{el_type}".')
            self.type = el_type

        nodes = self.nodes if nodes is None else nodes
        num_nodes = ShapeResolver.get_el_nodes_from_type(self.type)
        if len(nodes) != num_nodes:
            raise ValueError(f'Number of passed nodes "{len(nodes)}" does not match expected "{num_nodes}" ')

        self.nodes = nodes
        self._edges = None

    @property
    def edges_seq(self) -> np.ndarray | None:
        from .lines import line_edges
        from .shells import shell_edges
        from .solids import solid_edges

        edge_map = {
            ElemType.LINE: line_edges,
            ElemType.SHELL: shell_edges,
            ElemType.SOLID: solid_edges,
            SpringTypes.SPRING2: line_edges,
        }
        generalized_type = self.type
        edges_repo = edge_map[self.elem_type_group]
        if generalized_type not in edges_repo.keys():
            logger.error(f"Element type {self.type} is currently not supported")
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
            logger.error(f"Element type {self.type} is currently not supported")
            return None

        return solid_face_res

    @staticmethod
    def is_valid_elem(elem_type):
        valid_element_types = (
            LineShapes.get_all()
            + ShellShapes.get_all()
            + SolidShapes.get_all()
            + MassTypes.get_all()
            + SpringTypes.get_all()
            + ConnectorTypes.get_all()
        )
        valid_element_types_upper = [x.value.upper() for x in valid_element_types]
        value = elem_type.upper()
        if value in valid_element_types_upper:
            return True
        else:
            return False

    def __repr__(self):
        return f'{self.__class__.__name__}(Type: {self.type}, NodeIds: "{self.nodes}")'


def get_elem_type_group(el_type):
    if isinstance(el_type, SolidShapes):
        return ElemType.SOLID
    elif el_type in up(ShellShapes.get_all):
        return ElemType.SHELL
    elif el_type in up(LineShapes.get_all):
        return ElemType.LINE
    else:
        raise ValueError(f'Unrecognized Element Type: "{el_type}"')


def up(variables):
    return [v.upper() for v in variables]
