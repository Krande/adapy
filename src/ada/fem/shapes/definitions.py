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
    NONSTRUCTURAL = "NONSTRUCTURAL"


class SpringTypes(BaseShapeEnum):
    SPRING1 = "SPRING1"
    SPRING2 = "SPRING2"


class ShapeResolver:
    NUM_MAP = {
        # SPRING1 grounds a single node; SPRING2 joins two. Both used to sit under 1,
        # which contradicted ``line_edges[SPRING2] = [[0, 1]]``: readers that size a
        # record from this map (Sesam eltyp 40, Abaqus SPRING2) handed the walks a
        # one-node element, and asking it for its second end raised out of the
        # visualisation instead of drawing the spring.
        1: MassTypes.get_all() + [SpringTypes.SPRING1],
        2: [LineShapes.LINE, SpringTypes.SPRING2] + ConnectorTypes.get_all(),
        3: [LineShapes.LINE3, ShellShapes.TRI],
        4: [ShellShapes.QUAD, SolidShapes.TETRA],
        5: [SolidShapes.PYRAMID5],
        6: [ShellShapes.TRI6, SolidShapes.WEDGE],
        7: [ShellShapes.TRI7],
        8: [SolidShapes.HEX8, ShellShapes.QUAD8],
        9: [ShellShapes.QUAD9],
        10: [SolidShapes.TETRA10],
        13: [SolidShapes.PYRAMID13],
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

    # Enum members, not the strings these used to hold. An Elem's ``type`` is resolved
    # to an enum member by ShapeResolver, so a membership test against strings was
    # always False -- ``FEM.springs`` filters off ``springs``, and a filter that can
    # never match is worse than no filter.
    spring1n = [SpringTypes.SPRING1]
    spring2n = [SpringTypes.SPRING2]
    springs = spring1n + spring2n
    masses = MassTypes.get_all()
    connectors = ConnectorTypes.get_all()
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

        def quad_face_to_tris(q):
            return [(q[0], q[1], q[2]), (q[0], q[2], q[3])]

        if isinstance(self.type, SolidShapes):
            faces_seq = self.solids_face_seq
            # Generalised quad-to-tri split. Anything with 4-noded
            # face entries needs triangulating before the renderer
            # consumes it. Covers HEX8/HEX20/HEX27 (all-quad faces)
            # plus PYRAMID5/13 (one quad base + four triangle
            # sides). Mixed-arity face lists are fine — triangles
            # pass through unchanged.
            if faces_seq is not None:
                faces_seq = list(chain.from_iterable(quad_face_to_tris(f) if len(f) == 4 else [f] for f in faces_seq))
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


def is_renderable(el_type) -> bool:
    """Whether the visualisation walks can turn elements of this type into geometry.

    A cell block carries exactly one element type, so this is a per-block question:
    asking it once per block keeps it off the hot path of a model with millions of
    elements, and answering "no" there means ``ElemShape`` is never constructed for
    a type it would only refuse.

    Two reasons a type lands here, both with the same answer. Some have no geometry
    by construction — a point mass is a node, and a SPRING1 grounds a single node
    with no second end to draw a line to. Others are simply absent from the edge
    tables because nobody has added them yet. Either way the renderer has nothing to
    emit, and the honest response is to leave those elements out of the scene rather
    than to throw away the rest of the model along with them.

    Derived from the very tables ``ElemShape.edges_seq`` reads, so a type added to
    one of them becomes renderable here without a second edit.
    """
    from .lines import line_edges
    from .shells import shell_edges
    from .solids import solid_edges

    if isinstance(el_type, SolidShapes):
        return el_type in solid_edges
    if isinstance(el_type, ShellShapes):
        return el_type in shell_edges
    if isinstance(el_type, (LineShapes, ConnectorTypes, SpringTypes)):
        return el_type in line_edges

    # MassTypes, and anything outside the shape enums entirely: ElemShape.elem_type_group
    # has no group for these and raises rather than returning one.
    return False


def is_structural(el_type) -> bool:
    """Whether this type is ordinary mesh geometry a structural-element writer can emit.

    False for the point- and link-like types — masses, springs, connectors. Every deck
    format spells those as their own card family (``*Spring``, ``BNMASS``, ``NODEMASS``)
    rather than as a row in the element table, and none of them carries the FemSection a
    structural row is sized from. Writers route on this instead of finding out the hard
    way that ``ShapeResolver.to_geom_repr`` has no group for them.
    """
    return isinstance(el_type, (LineShapes, ShellShapes, SolidShapes))


def has_faces(el_type) -> bool:
    """Whether this type contributes triangles, or only edges.

    Lines, connectors and springs are one-dimensional: they draw as edges and asking
    them for faces reaches a face table that does not exist. Shells and solids are
    the only types with a surface to tessellate.
    """
    return isinstance(el_type, (ShellShapes, SolidShapes))


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
