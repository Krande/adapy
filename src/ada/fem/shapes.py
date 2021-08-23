import logging

import numpy as np

from . import Elem


class ElemShapes:
    """

    :param el_type:
    """

    # 2D elements
    tri = ["S3", "S3R", "R3D3"]
    quad = ["S4", "S4R", "R3D4"]
    quad8 = ["S8", "S8R"]
    quad6 = ["STRI65"]
    shell = tri + quad + quad8 + quad6
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
    bm2 = ["B31", "B32"]
    beam = bm2
    spring1n = ["SPRING1"]
    spring2n = ["SPRING2"]
    springs = spring1n + spring2n
    masses = ["MASS", "ROTARYI"]
    connectors = ["CONNECTOR", "CONN3D2"]
    other2n = connectors
    other = other2n

    @staticmethod
    def is_beam_elem(elem: Elem):
        if elem.type in ElemShapes.beam:
            return True
        else:
            return False

    @staticmethod
    def is_valid_elem(elem_type):
        value = elem_type.upper()
        if (
            value
            not in ElemShapes.shell
            + ElemShapes.volume
            + ElemShapes.beam
            + ElemShapes.springs
            + ElemShapes.masses
            + ElemShapes.other
        ):
            return False
        else:
            return True

    @staticmethod
    def num_nodes(el_name):
        if el_name in ElemShapes.masses + ElemShapes.spring1n:
            return 1
        elif el_name in ElemShapes.bm2 + ElemShapes.spring2n + ElemShapes.other2n:
            return 2
        elif el_name in ElemShapes.tri:
            return 3
        elif el_name in ElemShapes.quad + ElemShapes.tetrahedron:
            return 4
        elif el_name in ElemShapes.pyramid5:
            return 5
        elif el_name in ElemShapes.quad6 + ElemShapes.prism6:
            return 6
        elif el_name in ElemShapes.quad8 + ElemShapes.cube8:
            return 8
        elif el_name in ElemShapes.tetrahedron10:
            return 10
        elif el_name in ElemShapes.prism15:
            return 15
        elif el_name in ElemShapes.cube20:
            return 20
        elif el_name in ElemShapes.cube27:
            return 27
        else:
            raise ValueError(f'element type "{el_name}" is not yet supported')

    def __init__(self, el_type, nodes):
        self.type = None
        self.nodes = None
        self._edges = None
        self.update(el_type, nodes)

    @property
    def edges(self):
        from ada import Node

        if self.edges_seq is None:
            raise ValueError(f'Element type "{self.type}" is missing element node descriptions')
        if self._edges is None:
            if type(self.nodes[0]) is Node:
                self._edges = [self.nodes[e].p for ed_seq in self.edges_seq for e in ed_seq]
            else:
                self._edges = [self.nodes[e] for ed_seq in self.edges_seq for e in ed_seq]

            return self._edges

        else:
            return self._edges

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
    def faces(self):
        if self.type.upper() in ElemShapes.volume:
            faces = self._cube_faces_global
        elif self.type.upper() in ElemShapes.shell:
            faces = self._shell_faces
        else:
            raise ValueError(f'element type "{self.type}" is yet to be included')

        if faces is None:
            return None

        new_n = [[self.nodes[n[0]], self.nodes[n[1]], self.nodes[n[2]]] for n in faces]
        return new_n

    @property
    def _cube_faces_global(self):
        if self._cube_faces is None:
            return None
        new_n = []
        for n in self._cube_faces:
            new_n.append([self.nodes[n[0]], self.nodes[n[1]], self.nodes[n[2]]])
        return new_n

    @property
    def _cube_edges_global(self):
        if self._volume_edges is None:
            return None
        new_n = []
        for n in self._volume_edges:
            new_n.append([self.nodes[n[0]], self.nodes[n[1]]])
        return new_n

    @property
    def edges_seq(self):
        """

        :return:
        :rtype: numpy.ndarray
        """
        if self.type in self.volume:
            edges = self._volume_edges
        elif self.type in self.shell:
            edges = self._shell_edges
        elif self.type in self.beam:
            edges = self._beam_edges
        elif self.type in self.masses + self.spring1n:
            # These are point elements and have no edges
            return None
        elif self.type in self.spring2n + self.connectors:
            # To be implemented
            return None
        else:
            raise ValueError(f'Element type "{self.type}" is yet to be included')

        if edges is not None:
            return np.array(edges)
        else:
            return None

    @property
    def spring_edges(self):
        if self.type not in self.springs:
            return None
        springs = dict(SPRING2=[[0, 1]])
        return springs[self.type]

    @property
    def _beam_edges(self):
        r"""
                Line:                 Line3:          Line4:

              v
              ^
              |
              |
        0-----+-----1 --> u   0----2----1     0---2---3---1

        """
        if self.type not in self.beam:
            logging.error("A call was made to beam edges even though type is not beam")
            return None
        if self.type in ["B31", "SPRING2"]:
            return [[0, 1]]
        elif self.type == "B32":
            return [[0, 2, 1]]
        else:
            raise ValueError(f'Elem type "{self.type}" is not yet supported')

    @property
    def _shell_edges(self):
        r"""Quadrangle:            Quadrangle8:            Quadrangle9:

      v
      ^
      |
3-----------2          3-----6-----2           3-----6-----2
|     |     |          |           |           |           |
|     |     |          |           |           |           |
|     +---- | --> u    7           5           7     8     5
|           |          |           |           |           |
|           |          |           |           |           |
0-----------1          0-----4-----1           0-----4-----1

Triangle:               Triangle6:          Triangle9/10:          Triangle12/15:

v
^                                                                   2
|                                                                   | \
2                       2                    2                      9   8
|`\                     |`\                  | \                    |     \
|  `\                   |  `\                7   6                 10 (14)  7
|    `\                 5    `4              |     \                |         \
|      `\               |      `\            8  (9)  5             11 (12) (13) 6
|        `\             |        `\          |         \            |             \
0----------1 --> u      0-----3----1         0---3---4---1          0---3---4---5---1
"""
        if self.type not in self.shell:
            return None

        if self.type in ["S4", "S4R", "R3D4"]:
            return [[0, 1], [1, 2], [2, 3], [3, 0]]
        elif self.type in ["S3", "S3R"]:
            return [[0, 1], [1, 2], [2, 0]]
        else:
            raise ValueError(f'Elem type "{self.type}" is not yet supported')

    @property
    def _shell_faces(self):
        if self.type not in self.shell:
            return None

        if self.type.upper() in ["S4", "S4R"]:
            return [[0, 1, 2], [0, 2, 3]]
        elif self.type.upper() in ["S3", "S3R"]:
            return [[0, 1, 2]]
        else:
            logging.error(f'element type "{self.type}" is yet to be included')

    @property
    def _volume_edges(self):
        r"""
        Hexahedron:             Hexahedron20:          Hexahedron27:

       v
3----------2            3----13----2           3----13----2
|\     ^   |\           |\         |\          |\         |\
| \    |   | \          | 15       | 14        |15    24  | 14
|  \   |   |  \         9  \       11 \        9  \ 20    11 \
|   7------+---6        |   7----19+---6       |   7----19+---6
|   |  +-- |-- | -> u   |   |      |   |       |22 |  26  | 23|
0---+---\--1   |        0---+-8----1   |       0---+-8----1   |
 \  |    \  \  |         \  17      \  18       \ 17    25 \  18
  \ |     \  \ |         10 |        12|        10 |  21    12|
   \|      w  \|           \|         \|          \|         \|
    4----------5            4----16----5           4----16----5
        Tetrahedron:                          Tetrahedron10:

                   v
                 .
               ,/
              /
           2                                     2
         ,/|`\                                 ,/|`\
       ,/  |  `\                             ,/  |  `\
     ,/    '.   `\                         ,6    '.   `5
   ,/       |     `\                     ,/       8     `\
 ,/         |       `\                 ,/         |       `\
0-----------'.--------1 --> u         0--------4--'.--------1
 `\.         |      ,/                 `\.         |      ,/
    `\.      |    ,/                      `\.      |    ,9
       `\.   '. ,/                           `7.   '. ,/
          `\. |/                                `\. |/
             `3                                    `3
                `\.
                   ` w
        """
        if self.type not in self.volume:
            return None
        if self.type in ["C3D8", "C3D8R", "C3D8H"]:
            return [
                [0, 1],
                [1, 2],
                [2, 3],
                [3, 0],
                [0, 4],
                [4, 7],
                [7, 3],
                [3, 0],
                [4, 5],
                [5, 6],
                [6, 7],
                [1, 5],
                [2, 6],
            ]
        elif self.type in ["C3D4"]:
            return [(0, 1), (1, 3), (3, 0), (0, 2), (2, 3), (1, 2)]
        elif self.type in ["C3D5"]:
            return [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (1, 4), (2, 4), (3, 4)]
        elif self.type == "C3D10":
            return [
                [0, 7],
                [7, 3],
                [3, 9],
                [9, 1],
                [1, 4],
                [4, 0],
                [0, 6],
                [6, 2],
                [2, 5],
                [5, 1],
                [3, 8],
                [8, 2],
            ]
        elif self.type in ["C3D20", "C3D20R"]:
            # Abaqus
            return [
                (0, 8),
                (8, 1),
                (1, 17),
                (17, 5),
                (5, 12),
                (12, 4),
                (4, 15),
                (15, 7),
                (0, 11),
                (11, 3),
                (1, 9),
                (9, 2),
                (2, 18),
                (18, 6),
                (6, 14),
                (14, 7),
                (3, 19),
                (19, 7),
                (2, 10),
                (10, 3),
                (5, 13),
                (13, 6),
                (0, 16),
                (16, 4),
            ]
            # gmsh
            # return [
            #     (0, 9), (9, 3), (3, 13), (13, 2), (2, 11), (11, 1), (1, 8), (8, 0),
            #     (0, 10), (10, 4), (4, 17), (17, 7), (7, 15), (15, 3), (3, 9), (9, 0),
            #     (4, 16), (16, 5), (5, 10), (10, 6), (6, 19), (19, 7), (7, 17), (17, 4),
            #     (5, 12), (12, 1), (1, 11), (11, 2), (2, 14), (14, 6), (6, 18), (18, 5),
            #     (0,8), (8,1), (3,13), (13,2)
            # ]
        else:
            logging.error(f"Element type {self.type} is currently not supported")

    @property
    def _cube_faces(self):
        if self.type not in self.volume:
            return None
        if self.type.upper() == "C3D8":
            return [
                [0, 2, 3],
                [0, 1, 3],
                [0, 4, 7],
                [0, 7, 3],
                [0, 4, 5],
                [0, 5, 1],
                [2, 7, 6],
                [2, 3, 7],
                [5, 6, 7],
                [5, 7, 4],
                [5, 2, 1],
                [5, 6, 2],
            ]
        elif self.type.upper() == "C3D10":
            return [[0, 2, 3], [0, 1, 2], [1, 2, 3], [0, 2, 3]]
        else:
            logging.error(f"Element type {self.type} is currently not supported for visualization")

    def __repr__(self):
        return f'{self.__class__.__name__}(Type: {self.type}, NodeIds: "{self.nodes}")'
