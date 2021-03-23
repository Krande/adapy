import numpy as np

from . import constants as co
from .containers import FemElements, FemSections, FemSets

__all__ = [
    "Amplitude",
    "Bc",
    "Csys",
    "InteractionProperty",
    "Interaction",
    "ElemShapes",
    "Step",
    "Surface",
    "Elem",
    "Connector",
    "Constraint",
    "PredefinedField",
    "FemSet",
    "FEM",
    "Mass",
    "HistOutput",
    "FieldOutput",
    "ConnectorSection",
    "Load",
    "LoadCase",
    "FemSection",
    "Spring",
]


class FemBase:
    """

    :param name:
    :param metadata:
    :param parent:
    """

    def __init__(self, name, metadata, parent):
        self.name = name
        self.parent = parent
        self._metadata = metadata if metadata is not None else dict()

    @property
    def name(self):
        """

        :return:
        """
        return self._name

    @name.setter
    def name(self, value):
        if str.isnumeric(value[0]):
            raise ValueError("Name cannot start with numeric")
        self._name = value

    @property
    def parent(self):
        """

        :rtype: ada.fem.FEM
        """
        return self._parent

    @parent.setter
    def parent(self, value):
        # if type(value) not in (FEM, Step):
        #     raise ValueError(f'Parent type "{type(value)}" is not supported')
        self._parent = value

    @property
    def metadata(self):
        """

        :return:
        """
        return self._metadata

    @property
    def on_assembly_level(self):
        """

        :return:
        """
        # TODO: This is not really working correctly. This must be fixed
        from ada import Assembly

        return True if type(self.parent.parent) is Assembly else False

    @property
    def instance_name(self):
        """

        :return:
        """
        if self.on_assembly_level is False:
            return self.name
        else:
            return self.parent.instance_name + "." + self.name


class ElemShapes:
    """

    :param el_type:
    """

    tri = ["S3", "S3R", "R3D3"]
    quad = ["S4", "S4R", "R3D4"]
    quad8 = ["S8", "S8R"]
    quad6 = ["STRI65"]
    shell = tri + quad + quad8 + quad6
    cube8 = ["C3D8", "C3D8R", "C3D8H"]
    cube20 = ["C3D20", "C3D20R", "C3D20RH"]
    cube27 = ["C3D27"]
    pyramid4 = ["C3D4"]
    pyramid5 = ["C3D5"]
    pyramid10 = ["C3D10"]
    prism6 = ["C3D6"]
    prism15 = ["C3D15"]
    volume = cube8 + cube20 + pyramid10 + pyramid4 + pyramid5 + prism15 + prism6
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
    def num_nodes(el_name):
        if el_name in ElemShapes.masses + ElemShapes.spring1n:
            return 1
        elif el_name in ElemShapes.bm2 + ElemShapes.spring2n + ElemShapes.other2n:
            return 2
        elif el_name in ElemShapes.tri:
            return 3
        elif el_name in ElemShapes.quad + ElemShapes.pyramid4:
            return 4
        elif el_name in ElemShapes.pyramid5:
            return 5
        elif el_name in ElemShapes.quad6 + ElemShapes.prism6:
            return 6
        elif el_name in ElemShapes.quad8 + ElemShapes.cube8:
            return 8
        elif el_name in ElemShapes.pyramid10:
            return 10
        elif el_name in ElemShapes.prism15:
            return 15
        elif el_name in ElemShapes.cube20:
            return 20
        elif el_name in ElemShapes.cube27:
            return 27
        else:
            raise ValueError(f'element type "{el_name}" is not yet supported')

    def __init__(self, el_type):
        self.type = el_type

    @property
    def type(self):
        """

        :return: Element type.
        """
        return self._el_type

    @type.setter
    def type(self, value):
        value = value.upper()
        if (
            value
            not in ElemShapes.shell
            + ElemShapes.volume
            + ElemShapes.beam
            + ElemShapes.springs
            + ElemShapes.masses
            + ElemShapes.other
        ):
            raise ValueError(f'Currently unsupported element type "{value}".')
        self._el_type = value

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
        if self.type in ["S4", "S4R", "R3D4"]:
            return [[0, 1], [1, 2], [2, 3], [3, 0]]
        elif self.type in ["S3", "S3R"]:
            return [[0, 1], [1, 2], [2, 0]]
        else:
            raise ValueError(f'Elem type "{self.type}" is not yet supported')

    @property
    def _shell_faces(self):
        if self.type.upper() in ["S4", "S4R"]:
            return [[0, 1, 2], [0, 2, 3]]
        elif self.type.upper() in ["S3", "S3R"]:
            return [[0, 1, 2]]
        else:
            print('element type "{}" is yet to be included'.format(self.type))

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
            print("Element type {} is currently not supported".format(self.type))

    @property
    def _cube_faces(self):
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
            print("Element type {} is currently not supported".format(self.type))


class FEM(FemBase):
    """
    A FEM representation of its parent Part

    :param name: Name of analysis model
    :param parent: Part object
    :param metadata: Attached metadata
    """

    def __init__(self, name=None, parent=None, metadata=None):
        from ada.core.containers import Nodes

        metadata = metadata if metadata is not None else dict()
        metadata["sensor_data"] = dict()
        metadata["info"] = dict()
        super().__init__(name, metadata, parent)
        self._nodes = Nodes(parent=self)
        self._elements = FemElements(fem_obj=self)
        self._sets = FemSets(fem_obj=self)
        self._sections = FemSections(fem_obj=self)
        self._bcs = []
        self._masses = dict()
        self._constraints = []
        self._surfaces = dict()
        self._amplitudes = dict()
        self._steps = list()
        self._connectors = dict()
        self._connector_sections = dict()
        self._springs = dict()
        self._intprops = dict()
        self._interactions = dict()
        self._sensors = dict()
        self._predefined_fields = dict()
        self._subroutine = None
        self._initial_state = None
        self._lcsys = dict()

    def edit(self, parent=None, instance_name=None, initial_state=None):
        """

        :param parent:
        :param instance_name:
        :param initial_state:
        """
        self._parent = parent if parent is not None else self._parent
        self._name = instance_name if instance_name is not None else self._name
        self._initial_state = initial_state if initial_state is not None else self._initial_state

    def add_elem(self, elem):
        """

        :param elem:
        :type elem: Elem
        :return:
        """
        elem.parent = self
        self.elements.add(elem)

    def add_section(self, section):
        """

        :param section:
        :type section: FemSection
        """
        section.parent = self
        self.sections.add(section)

    def add_bc(self, bc):
        """
        Adds a BC to the assembly

        :param bc: Bc object
        :type bc: Bc
        """
        if bc.name in [b.name for b in self._bcs]:
            raise Exception('BC with name "{bc_id}" already exists'.format(bc_id=bc.name))
        bc.parent = self
        if bc.fem_set not in self.sets:
            self.sets.add(bc.fem_set)

        self._bcs.append(bc)

    def add_mass(self, mass):
        """

        :param mass:
        :type mass: Mass
        """
        mass.parent = self
        self._masses[mass.name] = mass

    def add_set(
        self,
        fem_set,
        ids=None,
        p=None,
        vol_box=None,
        vol_cyl=None,
        single_member=False,
        tol=1e-4,
    ):
        """
        Simple method that creates a set string based on a set name, node or element ids and adds it to the assembly str

        :param fem_set: A fem set object
        :param ids: List of integers
        :param p: Single point (x,y,z)
        :param vol_box: Search by quadratic volume. Where p is (xmin, ymin, zmin) and vol_box is (xmax, ymax, zmax)
        :param vol_cyl: Search by cylindrical volume. Used together with p to find
                        nodes within cylinder inputted by [radius, height, thickness]
        :param single_member: Set True if you wish to keep only a single member
        :param tol: Point Tolerances. Default is 1e-4
        :type fem_set: FemSet
        """
        if ids is not None:
            fem_set.add_members(ids)

        fem_set.parent = self

        def append_members(nodelist):
            if single_member is True:
                fem_set.add_members([nodelist[0]])
            else:
                fem_set.add_members(nodelist)

        if fem_set.type == "nset":
            if p is not None or vol_box is not None or vol_cyl is not None:
                nodes = self.nodes.get_by_volume(p, vol_box, vol_cyl, tol)
                if len(nodes) == 0 and self.parent is not None:
                    assembly = self.parent.get_assembly()
                    list_of_ps = assembly.get_all_subparts() + [assembly]
                    for part in list_of_ps:
                        nodes = part.fem.nodes.get_by_volume(p, vol_box, vol_cyl, tol)
                        if len(nodes) > 0:
                            fem_set.parent = part.fem
                            append_members(nodes)
                            part.fem.add_set(fem_set)
                            return fem_set

                    raise Exception(f'No nodes found for fem set "{fem_set.name}"')
                elif nodes is not None and len(nodes) > 0:
                    append_members(nodes)
                else:
                    raise Exception(f'No nodes found for femset "{fem_set.name}"')

        self._sets.add(fem_set)
        return fem_set

    def add_step(self, step):
        """
        Adds a analysis step to the assembly

        :param step: Step Object
        :type step: Step
        """
        if len(self._steps) > 0:
            if self._steps[-1].type != "eigenfrequency" and step.type == "complex_eig":
                raise Exception(
                    "complex eigenfrequency analysis step needs to follow eigenfrequency step. Check your input"
                )
        step.parent = self
        self._steps.append(step)

        return step

    def add_interaction_property(self, int_prop):
        """

        :param int_prop:
        :type int_prop: InteractionProperty
        """
        int_prop.parent = self
        self._intprops[int_prop.name] = int_prop

    def add_interaction(self, interaction):
        """

        :param interaction:
        :type interaction: Interaction
        """
        interaction.parent = self
        self._interactions[interaction.name] = interaction

    def add_constraint(self, constraint):
        """
        Method for adding a tie or coupling in the assembly

        :param constraint: Name of connection
        :type constraint: Constraint
        """
        constraint.parent = self
        self._constraints.append(constraint)

    def add_lcsys(self, lcsys):
        """

        :param lcsys:
        :type lcsys: ada.fem.Csys
        :return:
        """
        if lcsys.name in self._lcsys.keys():
            raise ValueError("Local Coordinate system cannot have duplicate name")
        lcsys.parent = self
        self._lcsys[lcsys.name] = lcsys

    def add_connector_section(self, connector_section):
        """
        Adds a connector section to the assembly

        :param connector_section:
        :type connector_section: ConnectorSection
        """
        connector_section.parent = self
        self._connector_sections[connector_section.name] = connector_section

    def add_connector(self, connector):
        """
        Add a connector to the assembly using the :func:`~abaqus.utils.add_connector_elem`

        :param connector: Name of connector
        :type connector: Connector

        """
        connector.parent = self
        self._connectors[connector.name] = connector
        connector.csys.parent = self
        self.elements.add(connector)
        self.add_set(FemSet(name=connector.name, members=[connector.id], set_type="elset"))

    def add_rp(self, name, node):
        """
        Adds a reference point in assembly

        :param node: Node
        :param name: Creates a set with by specifying a name
        :type node: ada.Node
        """
        node.parent = self
        self.nodes.add(node)
        fem_set = self.add_set(FemSet(name, [node], "nset"))
        return node, fem_set

    def add_surface(self, surface):
        """

        :param surface:
        :type surface: Surface
        """
        surface.parent = self
        self._surfaces[surface.name] = surface

    def add_amplitude(self, amplitude):
        """
        Adds amplitude data history to the assembly


        :param amplitude: Amplitude object
        :type amplitude: Amplitude
        """
        amplitude.parent = self
        self._amplitudes[amplitude.name] = amplitude

    def add_sensor(self, name, point, comment, tol=1e-2):
        """

        :param name: Name of coordinate set
        :param point: Sensor Coordinate
        :param comment: Comment
        :param tol:
        """
        fem_set = FemSet(name, [], "nset", metadata=dict(comment=comment))
        self.add_set(fem_set, p=point, tol=tol, single_member=True)
        if name in self._sensors.keys():
            raise Exception("{} exists in sensor sets and will be overwritten. Please change name.".format(name))

        self._sensors[name] = fem_set
        # self._cad[name].add_shape(ada.cad.utils.make_sphere(point, tol), colour='red', transparency=0.5)

    def add_predefined_field(self, pre_field):
        """

        :type pre_field: PredefinedField
        """
        pre_field.parent = self

        self._predefined_fields[pre_field.name] = pre_field

    def add_spring(self, spring):
        """

        :param spring:
        :type spring: Spring
        """

        # self.elements.add(spring)

        if spring.fem_set.parent is None:
            self.sets.add(spring.fem_set)
        self._springs[spring.name] = spring

    def convert_ecc_to_mpc(self):
        """

        Converts beam offsets to MPC constraints
        """
        from ada import Node
        from ada.core.utils import vector_length

        edited_nodes = dict()
        tol = 1e-3

        def build_mpc(fs):
            """

            :param fs:
            :type fs: FemSection
            :return:
            """
            if fs.offset is None or fs.type != "beam":
                return
            elem = fs.elset.members[0]
            for n_old, ecc in fs.offset:
                i = elem.nodes.index(n_old)
                if n_old.id in edited_nodes.keys():
                    n_new = edited_nodes[n_old.id]
                    mat = np.eye(3)
                    new_p = np.dot(mat, ecc) + n_old.p
                    n_new_ = Node(new_p, parent=elem.parent)
                    if vector_length(n_new_.p - n_new.p) > tol:
                        elem.parent.nodes.add(n_new_, allow_coincident=True)
                        m_set = FemSet(f"el{elem.id}_mpc{i + 1}_m", [n_new_], "nset")
                        s_set = FemSet(f"el{elem.id}_mpc{i + 1}_s", [n_old], "nset")
                        c = Constraint(
                            f"el{elem.id}_mpc{i + 1}_co",
                            "mpc",
                            m_set,
                            s_set,
                            mpc_type="Beam",
                            parent=elem.parent,
                        )
                        elem.parent.add_constraint(c)
                        elem.nodes[i] = n_new_
                        edited_nodes[n_old.id] = n_new_

                    else:
                        elem.nodes[i] = n_new
                        edited_nodes[n_old.id] = n_new
                else:
                    mat = np.eye(3)
                    new_p = np.dot(mat, ecc) + n_old.p
                    n_new = Node(new_p, parent=elem.parent)
                    elem.parent.nodes.add(n_new, allow_coincident=True)
                    m_set = FemSet(f"el{elem.id}_mpc{i + 1}_m", [n_new], "nset")
                    s_set = FemSet(f"el{elem.id}_mpc{i + 1}_s", [n_old], "nset")
                    c = Constraint(
                        f"el{elem.id}_mpc{i + 1}_co",
                        "mpc",
                        m_set,
                        s_set,
                        mpc_type="Beam",
                        parent=elem.parent,
                    )
                    elem.parent.add_constraint(c)

                    elem.nodes[i] = n_new
                    edited_nodes[n_old.id] = n_new

        list(map(build_mpc, filter(lambda x: x.offset is not None, self.sections)))

    def convert_hinges_2_couplings(self):
        """
        Convert beam hinges to coupling constraints
        """
        from ada import Node

        def converthinges(fs):
            """

            :param fs:
            :type fs: ada.fem.FemSection
            """
            if fs.hinges is None or fs.type != "beam":
                return
            elem = fs.elset.members[0]
            assert isinstance(elem, Elem)

            for n, d, csys in fs.hinges:
                n2 = Node(n.p, None, parent=elem.parent)
                elem.parent.nodes.add(n2, allow_coincident=True)
                i = elem.nodes.index(n)
                elem.nodes[i] = n2
                s_set = FemSet(f"el{elem.id}_hinge{i + 1}_s", [n], "nset")
                m_set = FemSet(f"el{elem.id}_hinge{i + 1}_m", [n2], "nset")
                elem.parent.add_set(m_set)
                elem.parent.add_set(s_set)
                c = Constraint(
                    f"el{elem.id}_hinge{i + 1}_co",
                    "coupling",
                    m_set,
                    s_set,
                    d,
                    csys=csys,
                )
                elem.parent.add_constraint(c)

        list(map(converthinges, filter(lambda x: x.hinges is not None, self.sections)))

    @property
    def parent(self):
        """

        :rtype: ada.Part
        """
        return self._parent

    @parent.setter
    def parent(self, value):
        from ada import Part

        if issubclass(type(value), Part) is False and value is not None:
            raise ValueError()
        self._parent = value

    @property
    def nodes(self):
        """

        :return:
        :rtype: ada.core.containers.Nodes
        """
        return self._nodes

    @property
    def elements(self):
        """

        :return:
        :rtype: FemElements
        """
        return self._elements

    @property
    def sections(self):
        """

        :return:
        :rtype: FemSections
        """
        return self._sections

    @property
    def bcs(self):
        return self._bcs

    @property
    def constraints(self):
        return self._constraints

    @property
    def instance_name(self):
        return self._name if self._name is not None else f"{self.parent.name}-1"

    @property
    def sets(self):
        """

        :return:
        :rtype: ada.fem.containers.FemSets
        """
        return self._sets

    @property
    def nsets(self):
        return self.sets.nodes

    @property
    def elsets(self):
        return self.sets.elements

    @property
    def masses(self):
        return self._masses

    @property
    def interactions(self):
        """

        :rtype: dict
        """
        return self._interactions

    @property
    def intprops(self):
        return self._intprops

    @property
    def steps(self):
        """

        :return:
        :rtype: list
        """
        return self._steps

    @property
    def surfaces(self):
        """

        :rtype: dict
        """
        return self._surfaces

    @property
    def connectors(self):
        return self._connectors

    @property
    def connector_sections(self):
        return self._connector_sections

    @property
    def amplitudes(self):
        return self._amplitudes

    @property
    def sensors(self):
        return self._sensors

    @property
    def predefined_fields(self):
        return self._predefined_fields

    @property
    def initial_state(self):
        """

        :return:
        :rtype: PredefinedField
        """
        return self._initial_state

    @property
    def springs(self):
        return self._springs

    @property
    def lcsys(self):
        return self._lcsys

    def __repr__(self):
        return f"FEM({self.name}, Elements: {len(self.elements)}, Nodes: {len(self.nodes)})"


class FemSection(FemBase):
    """

    :param name:
    :param sec_type:
    :param elset:
    :param material:
    :param section:
    :param thickness:
    :param int_points:
    :type elset: FemSet
    :type material: ada.Material
    :type section: ada.Section
    """

    def __init__(
        self,
        name,
        sec_type,
        elset,
        material,
        section=None,
        local_z=None,
        local_y=None,
        thickness=None,
        int_points=5,
        offset=None,
        hinges=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._sec_type = sec_type
        if sec_type is None:
            raise ValueError("Section type cannot be None")
        self._elset = elset
        self._material = material
        self._section = section
        self._local_z = local_z
        self._local_y = local_y
        self._thickness = thickness
        self._int_points = int_points
        self._offset = offset
        self._hinges = hinges

    def link_elements(self):
        def link_elem(el):
            """

            :param el:
            :type el: Elem
            """
            el.fem_sec = self

        list(map(link_elem, self.elset.members))

    def get_offset_coords(self):
        """

        :param self:
        :type self: FemSection
        :return:
        """

        elem = self.elset.members[0]
        nodes = [n.p for n in elem.nodes]
        if self.offset is None:
            return nodes

        for n_old, ecc in self.offset:
            mat = np.eye(3)
            new_p = np.dot(mat, ecc) + n_old.p
            i = elem.nodes.index(n_old)
            nodes[i] = new_p

        return nodes

    @property
    def type(self):
        return self._sec_type

    @property
    def elset(self):
        return self._elset

    @property
    def local_z(self):
        """

        :return: Local Z describes the up vector of the cross section
        :rtype: list
        """
        from ada.core.utils import roundoff, unit_vector, vector_length

        if self._local_z is None:
            if self.type == "beam":
                n1, n2 = self.elset.members[0].nodes[0], self.elset.members[0].nodes[-1]
                v = n2.p - n1.p
                if vector_length(v) == 0.0:
                    xvec = [1, 0, 0]
                else:
                    xvec = unit_vector(v)

                crossed = np.cross(xvec, self.local_y)
                ma = max(abs(crossed))
                self._local_z = tuple([roundoff(x / ma, 3) for x in crossed])
            else:
                from ada.core.utils import normal_to_points_in_plane

                self._local_z = normal_to_points_in_plane([n.p for n in self.elset.members[0].nodes])
                # raise NotImplementedError("Local Z is not implemented for shell elements, yet.")
        return self._local_z

    @property
    def local_y(self):
        """

        :return: Local Z describes the up vector of the cross section
        :rtype: list
        """
        from ada.core.utils import roundoff, unit_vector, vector_length

        if self._local_y is None:
            if self.type == "beam":
                n1, n2 = self.elset.members[0].nodes[0], self.elset.members[0].nodes[-1]
                v = n2.p - n1.p
                if vector_length(v) == 0.0:
                    xvec = [1, 0, 0]
                else:
                    xvec = unit_vector(v)

                crossed = np.cross(xvec, self.local_z)
                ma = max(abs(crossed))
                self._local_y = tuple([roundoff(x / ma, 3) for x in crossed])
            else:
                raise NotImplementedError("Local Y is not implemented for shell elements, yet.")
        return self._local_y

    @property
    def section(self):
        """

        :return:
        :rtype: ada.Section
        """
        return self._section

    @property
    def material(self):
        """

        :return:
        :rtype: ada.Material
        """
        return self._material

    @property
    def thickness(self):
        return self._thickness

    @property
    def int_points(self):
        return self._int_points

    @property
    def offset(self):
        return self._offset

    @property
    def hinges(self):
        return self._hinges

    def __eq__(self, other):
        for key, val in self.__dict__.items():
            if "parent" in key:
                continue
            # Re-evaluate the elset exemption. Maybe this should raise False based on the fem set only?
            # if 'elset' in key:
            #     for m in self.__dict__[key].members:
            #         if m.type != other.__dict__[key].members[0].type:
            #             return False
            if other.__dict__[key] != val:
                return False

        return True

    def __repr__(self):
        return (
            f'FemSection({self.type} - name: "{self.name}", sec: "{self.section}", '
            f'mat: "{self.material}",  elset: "{self.elset}")'
        )


class FemSet(FemBase):
    """

    :param name: Name of Set
    :param members: Set Members
    :param set_type: Type of set (either 'nset' or 'elset')
    :param metadata: Metadata for object
    :param parent: Parent object
    """

    _valid_types = ["nset", "elset"]

    def __init__(self, name, members, set_type, metadata=None, parent=None):
        super().__init__(name, metadata, parent)
        self._set_type = set_type
        if self.type not in FemSet._valid_types:
            raise ValueError(f'set type "{set_type}" is not valid')
        self._members = members

    def __len__(self):
        return len(self._members)

    def __contains__(self, item):
        return item.id in self._members

    def __getitem__(self, index):
        return self._members[index]

    def __add__(self, other):
        """

        :param other:
        :type other: FemSet
        :return:
        """
        self.add_members(other.members)
        return self

    def add_members(self, members):
        """

        :param members:
        :type members: list
        """

        self._members += members

    @property
    def type(self):
        """

        :return: Type of set
        """
        return self._set_type.lower()

    @property
    def members(self):
        """

        :return: Members of set
        """
        return self._members

    @property
    def instance_num(self):
        """

        :return:
        """
        if self.on_assembly_level is True:
            return ",".join([f"{m}" for m in self.members])
        else:
            return ",".join(["{}.{}".format(self.parent.instance_name, m) for m in self.members])

    def __repr__(self):
        return f'FemSet({self.name}, type: "{self.type}", members: "{len(self.members)}")'


class Amplitude(FemBase):
    """

    :param name:
    :param x:
    :param y:
    :param smooth:
    :param metadata:
    :param parent:
    """

    def __init__(self, name, x, y, smooth=None, metadata=None, parent=None):
        super().__init__(name, metadata, parent)
        self._x = x
        self._y = y
        self._smooth = smooth

    @property
    def x(self):
        return self._x

    @property
    def y(self):
        return self._y

    @property
    def smooth(self):
        return self._smooth


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
    :type fem_set: FemSet
    """

    _valid_surf_types = ["ELEMENT", "NODE"]

    def __init__(
        self,
        name,
        surf_type,
        fem_set,
        weight_factor=None,
        face_id_label=None,
        id_refs=None,
        parent=None,
        metadata=None,
    ):
        super().__init__(name, metadata, parent)

        if surf_type not in self._valid_surf_types:
            raise ValueError(f'Surface type "{surf_type}" is currently not supported\\implemented')

        self._surf_type = surf_type
        self._fem_set = fem_set
        self._weight_factor = weight_factor
        self._face_id_label = face_id_label
        self._id_refs = id_refs

    @property
    def type(self):
        return self._surf_type

    @property
    def fem_set(self):
        return self._fem_set

    @property
    def weight_factor(self):
        return self._weight_factor

    @property
    def face_id_label(self):
        return self._face_id_label

    @property
    def id_refs(self):
        return self._id_refs


class Elem(FemBase, ElemShapes):
    """
    Node numbering of elements is based on GMSH doc here http://gmsh.info/doc/texinfo/gmsh.html#Node-ordering

    :param el_id:
    :param nodes:
    :param el_type:
    :param elset:
    :param fem_sec:
    :param mass_props:
    :param parent:
    :param metadata:
    :type fem_sec: FemSection
    """

    def __init__(
        self,
        el_id,
        nodes,
        el_type,
        elset=None,
        fem_sec=None,
        mass_props=None,
        parent=None,
        metadata=None,
    ):
        from ada import Node

        super().__init__(el_id, metadata, parent)
        super(FemBase, self).__init__(el_type)
        self._el_id = el_id
        num_nodes = self.num_nodes(self.type)
        if len(nodes) != num_nodes:
            raise ValueError(f'Number of passed nodes "{len(nodes)}" does not match expected "{num_nodes}" ')

        if type(nodes[0]) is Node:
            for node in nodes:
                node.refs.append(self)

        self._nodes = nodes
        self._elset = elset
        self._edges = None
        self._fem_sec = fem_sec
        self._mass_props = mass_props

    @property
    def name(self):
        """

        :return:
        """
        return self._name

    @name.setter
    def name(self, value):
        self._name = value

    @property
    def id(self):
        return self._el_id

    @id.setter
    def id(self, value):
        if type(value) not in (np.int32, int, np.uint64) and issubclass(type(self), Connector) is False:
            raise ValueError(f'Element name type "{type(value)}" must be numeric')
        self._el_id = value

    @property
    def nodes(self):
        return self._nodes

    @property
    def elset(self):
        return self._elset

    @property
    def edges(self):
        if self.edges_seq is None:
            raise ValueError(f'Element type "{self.type}" is missing element node descriptions')
        if self._edges is None:
            self._edges = [self.nodes[e].p for ed_seq in self.edges_seq for e in ed_seq]
            return self._edges
        else:
            return self._edges

    @property
    def faces(self):
        if self.type.upper() in ElemShapes.volume:
            faces = self._cube_faces_global
        elif self.type.upper() in ElemShapes.shell:
            faces = self._shell_faces
        else:
            raise ValueError('element type "{}" is yet to be included'.format(self.type))

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
    def fem_sec(self):
        """

        :return:
        :rtype: ada.fem.FemSection
        """
        return self._fem_sec

    @fem_sec.setter
    def fem_sec(self, value):
        self._fem_sec = value

    @property
    def mass_props(self):
        """

        :return:
        :rtype: ada.fem.Mass
        """
        return self._mass_props

    @mass_props.setter
    def mass_props(self, value):
        self._mass_props = value

    def __repr__(self):
        return f'Elem(ID: {self._el_id}, Type: {self.type}, NodeIds: "{self.nodes}")'


class Connector(Elem):
    """
    A Connector Element

    :param name:
    :param con_type:
    :param con_sec:
    :param el_id:
    :param n1:
    :param n2:
    :param csys:

    :type n1: ada.Node
    :type n2: ada.Node
    :type con_sec: ConnectorSection
    :type csys: Csys
    """

    def __init__(
        self,
        name,
        el_id,
        n1,
        n2,
        con_type,
        con_sec,
        preload=None,
        csys=None,
        metadata=None,
        parent=None,
    ):
        from ada import Node

        if type(n1) is not Node or type(n2) is not Node:
            raise ValueError("Connector Start\\end must be nodes")
        super(Connector, self).__init__(el_id, [n1, n2], "Connector")
        super(Elem, self).__init__(name, metadata, parent)
        self._n1 = n1
        self._n2 = n2
        self._con_type = con_type
        self._con_sec = con_sec
        self._preload = preload
        self._csys = csys if csys is not None else Csys(f"{name}_csys")

    @property
    def con_type(self):
        return self._con_type

    @property
    def con_sec(self):
        """

        :return:
        :rtype: ConnectorSection
        """
        return self._con_sec

    @property
    def n1(self):
        """

        :return:
        :rtype: ada.Node
        """
        return self._n1

    @property
    def n2(self):
        """

        :return:
        :rtype: ada.Node
        """
        return self._n2

    @property
    def csys(self):
        """

        :return:
        :rtype: Csys
        """
        return self._csys

    def __repr__(self):
        return f'ConnectorElem(ID: {self.id}, Type: {self.type}, End1: "{self.n1}", End2: "{self.n2}")'


class Spring(Elem):
    """

    :param name:
    :param el_id:
    :param el_type:
    :param stiff:
    :param n1:
    :param n2:
    :param metadata:
    :param parent:
    """

    def __init__(self, name, el_id, el_type, stiff, n1, n2=None, metadata=None, parent=None):
        nids = [n1]
        if n2 is not None:
            nids += [n2]
        super(Spring, self).__init__(el_id, nids, el_type)
        super(Elem, self).__init__(name, metadata, parent)
        self._stiff = stiff
        self._n1 = n1
        self._n2 = n2
        self._fem_set = FemSet(self.name + "_set", [el_id], "elset")
        if self.parent is not None:
            self.parent.sets.add(self._fem_set)

    @property
    def fem_set(self):
        return self._fem_set

    @property
    def stiff(self):
        return self._stiff

    def __repr__(self):
        return f'Spring("{self.name}", type="{self._stiff}")'


class Csys(FemBase):
    """

    :param name:
    :param nodes:
    :param coords:
    :param metadata:
    :param parent:
    """

    _valid_systems = ["RECTANGULAR"]  # , 'CYLINDRICAL', 'SPHERICAL', 'Z RECTANGULAR', 'USER']
    _valid_defs = ["COORDINATES", "NODES"]  # ,'OFFSET TO NODES'

    def __init__(
        self,
        name,
        definition="COORDINATES",
        system="RECTANGULAR",
        nodes=None,
        coords=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._definition = definition
        self._system = system
        self._nodes = nodes
        self._coords = coords

    @property
    def definition(self):
        return self._definition

    @property
    def system(self):
        return self._system

    @property
    def nodes(self):
        return self._nodes

    @property
    def coords(self):
        return self._coords


class ConnectorSection(FemBase):
    """
    A Connector Section


    :param name:
    :param elastic_comp:
    :param damping_comp:
    :param plastic_comp:
    :param rigid_dofs:
    :param soft_elastic_dofs:
    """

    def __init__(
        self,
        name,
        elastic_comp,
        damping_comp,
        plastic_comp=None,
        rigid_dofs=None,
        soft_elastic_dofs=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._elastic_comp = elastic_comp if elastic_comp is not None else []
        self._damping_comp = damping_comp if damping_comp is not None else []
        self._plastic_comp = plastic_comp
        self._rigid_dofs = rigid_dofs
        self._soft_elastic_dofs = soft_elastic_dofs

    @property
    def elastic_comp(self):
        return self._elastic_comp

    @property
    def damping_comp(self):
        return self._damping_comp

    @property
    def plastic_comp(self):
        return self._plastic_comp

    @property
    def rigid_dofs(self):
        return self._rigid_dofs

    @property
    def soft_elastic_dofs(self):
        return self._soft_elastic_dofs


class Constraint(FemBase):
    """
    A Constraint

    :param name:
    :param con_type:
    :param m_set:
    :param s_set:
    :param dofs:
    :param pos_tol:
    :param mpc_type:
    :type m_set: FemSet
    :type s_set: FemSet
    """

    def __init__(
        self,
        name,
        con_type,
        m_set,
        s_set,
        dofs=None,
        pos_tol=None,
        mpc_type=None,
        csys=None,
        parent=None,
        metadata=None,
    ):
        super().__init__(name, metadata, parent)
        self._con_type = con_type
        self._m_set = m_set
        self._s_set = s_set
        self._dofs = [1, 2, 3, 4, 5, 6] if dofs is None else dofs
        self._pos_tol = pos_tol
        self._mpc_type = mpc_type
        self._csys = csys

    @property
    def type(self):
        return self._con_type

    @property
    def m_set(self):
        """

        :return:
        :rtype: FemSet
        """
        return self._m_set

    @property
    def s_set(self):
        """

        :return:
        :rtype: FemSet
        """
        return self._s_set

    @property
    def dofs(self):
        return self._dofs

    @property
    def pos_tol(self):
        return self._pos_tol

    @property
    def csys(self):
        """

        :rtype: Csys
        """
        return self._csys

    @property
    def mpc_type(self):
        return self._mpc_type

    def __repr__(self):
        return f'Constraint("{self.type}", m: "{self.m_set.name}", s: "{self.s_set.name}", dofs: "{self.dofs}")'


class InteractionProperty(FemBase):
    """

    :param name:
    :param friction:
    :param pressure_overclosure:
    :param tabular:
    :param metadata:
    :param parent:
    """

    _valid_po = ["HARD", "TABULAR", "PENALTY"]

    def __init__(
        self,
        name,
        friction=None,
        pressure_overclosure="HARD",
        tabular=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._friction = friction if friction is not None else 0.0
        self._pressure_overclosure = pressure_overclosure
        if self.pressure_overclosure not in InteractionProperty._valid_po:
            raise ValueError(f'Pressure overclosure type "{pressure_overclosure}" is not supported')
        self._tabular = tabular

    @property
    def friction(self):
        return self._friction

    @property
    def pressure_overclosure(self):
        return self._pressure_overclosure.strip()

    @property
    def tabular(self):
        return self._tabular


class Interaction(FemBase):
    """"""

    _valid_contact_types = ["SURFACE", "GENERAL"]
    _valid_surface_types = ["SURFACE TO SURFACE"]

    def __init__(
        self,
        name,
        contact_type,
        surf1,
        surf2,
        int_prop,
        constraint=None,
        surface_type="SURFACE TO SURFACE",
        parent=None,
        metadata=None,
    ):
        """

        :param name:
        :param surf1:
        :param surf2:
        :param int_prop:
        :param constraint:
        :param surface_type: Interaction type.
        :type name: str
        :type int_prop: InteractionProperty
        :type constraint: str
        :type surf1: Surface
        :type surf2: Surface
        :type surface_type: str
        :type parent: FEM
        :type metadata: dict
        """
        super().__init__(name, metadata, parent)

        self.type = contact_type
        self.surface_type = surface_type
        self._surf1 = surf1
        self._surf2 = surf2
        self._int_prop = int_prop
        self._constraint = constraint

    @property
    def parent(self):
        """

        :rtype: ada.fem.FEM
        """
        return self._parent

    @parent.setter
    def parent(self, value):
        if type(value) not in (FEM, Step) and value is not None:
            raise ValueError(f'Parent type "{type(value)}" is not supported')
        self._parent = value

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.upper() not in self._valid_contact_types:
            raise ValueError(f'Contact type cannot be "{value}". Must be in {self._valid_contact_types}')
        self._type = value.upper()

    @property
    def surf1(self):
        """

        :rtype: Surface
        """
        return self._surf1

    @property
    def surf2(self):
        """

        :rtype: Surface
        """
        return self._surf2

    @property
    def interaction_property(self):
        """

        :return:
        :rtype: InteractionProperty
        """
        return self._int_prop

    @property
    def constraint(self):
        return self._constraint

    @property
    def surface_type(self):
        return self._surface_type

    @surface_type.setter
    def surface_type(self, value):
        if value not in self._valid_surface_types:
            raise ValueError(f'Surface type cannot be "{value}". Must be in {self._valid_surface_types}')
        self._surface_type = value


class PredefinedField(FemBase):
    """

    :param name:
    :param field_type:
    :param fem_set:
    :param dofs:
    :param magnitude:
    :param metadata:
    :param parent:
    """

    valid_types = ["VELOCITY", "INITIAL STATE"]

    def __init__(
        self,
        name,
        field_type,
        fem_set=None,
        dofs=None,
        magnitude=None,
        initial_state_file=None,
        initial_state_part=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self.type = field_type
        self._fem_set = fem_set
        self._dofs = dofs
        self._magnitude = magnitude
        self._initial_state_part = initial_state_part
        self._initial_state_file = initial_state_file
        if self.initial_state_file is not None:
            self.initial_state_part.fem.edit(initial_state=self)

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.upper() not in PredefinedField.valid_types:
            raise ValueError(f'The field type "{value.upper()}" is currently not supported')
        self._type = value.upper()

    @property
    def fem_set(self):
        """

        :return:
        :rtype: FemSet
        """
        return self._fem_set

    @property
    def dofs(self):
        return self._dofs

    @property
    def magnitude(self):
        return self._magnitude

    @property
    def initial_state_part(self):
        return self._initial_state_part

    @property
    def initial_state_file(self):
        return self._initial_state_file


class Bc(FemBase):
    """

    :param name:
    :param fem_set:
    :param dofs:
    :param magnitudes:
    :param bc_type:
    :param amplitude_name:
    :param init_condition: List of tuples [(dof1, magnitude1), (dof2, magnitude2)]
    :type fem_set: FemSet
    """

    _valid_types = [
        "displacement",
        "velocity",
        "connector_displacement",
        "symmetry/antisymmetry/encastre",
        "displacement/rotation",
        "velocity/angular velocity",
    ]

    def __init__(
        self,
        name,
        fem_set,
        dofs,
        magnitudes=None,
        bc_type="displacement",
        amplitude_name=None,
        init_condition=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._fem_set = fem_set
        self._dofs = dofs if type(dofs) is list else [dofs]
        if magnitudes is None:
            self._magnitudes = [None] * len(self._dofs)
        else:
            self._magnitudes = magnitudes if type(magnitudes) is list else [magnitudes]
        self.type = bc_type
        self._amplitude_name = amplitude_name
        self._init_condition = init_condition

    def add_init_condition(self, init_condition):
        self._init_condition = init_condition

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.lower() not in self._valid_types:
            raise ValueError(f'BC type "{value}" is not yet supported')
        self._type = value.lower()

    @property
    def fem_set(self):
        """

        :return:
        :rtype: FemSet
        """
        return self._fem_set

    @property
    def dofs(self):
        return self._dofs

    @property
    def magnitudes(self):
        return self._magnitudes

    @property
    def amplitude_name(self):
        return self._amplitude_name


class Mass(FemBase):
    """

    :param name:
    :param fem_set:
    :param mass:
    :param mass_type:
    :param ptype:
    :param units:
    :param metadata:
    :param parent:
    :type fem_set: FemSet
    """

    _valid_types = ["MASS", "NONSTRUCTURAL MASS", "ROTARY INERTIA"]

    def __init__(
        self,
        name,
        fem_set,
        mass,
        mass_type=None,
        ptype=None,
        units=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._fem_set = fem_set
        if mass is None:
            raise ValueError("Mass cannot be None")
        self._mass = mass
        self._mass_type = mass_type if mass_type is not None else "MASS"
        if self.type not in Mass._valid_types:
            raise ValueError(f'Mass type "{self.type}" is not supported')
        self.point_mass_type = ptype
        self._units = units

    @property
    def type(self):
        return self._mass_type.upper()

    @property
    def fem_set(self):
        """
        :rtype: FemSet
        """
        return self._fem_set

    @property
    def mass(self):
        if self.point_mass_type is None:
            if self.type == "MASS":
                if (len(self._mass) == 1) is False:
                    raise ValueError("Mass can only be a scalar number for Isotropic mass")
                return self._mass[0]
            else:
                return self._mass
        elif self.point_mass_type == "ISOTROPIC":
            if (len(self._mass) == 1) is False:
                raise ValueError("Mass can only be a scalar number for Isotropic mass")
            return self._mass[0]
        elif self.point_mass_type == "ANISOTROPIC":
            if (len(self._mass) == 3) is False:
                raise ValueError("Mass must be specified for 3 dofs for Anisotropic mass")
            return self._mass
        else:
            raise ValueError(f'Unknown mass input "{self.type}"')

    @property
    def units(self):
        return self._units

    @property
    def point_mass_type(self):
        return self._ptype

    @point_mass_type.setter
    def point_mass_type(self, value):
        self._ptype = value

    def __repr__(self):
        return f"Mass({self.name}, {self.point_mass_type}, [{self.mass}])"


class HistOutput(FemBase):
    """

    :param name: Unique History Output Name
    :param fem_set: Set name associated for history output
    :param set_type:
    :param variables:
    :param int_type: Interval type
    :type name: str
    :type set_type: str
    :type variables: list

    """

    default_hist = [
        "ALLAE",
        "ALLCD",
        "ALLDMD",
        "ALLEE",
        "ALLFD",
        "ALLIE",
        "ALLJD",
        "ALLKE",
        "ALLKL",
        "ALLPD",
        "ALLQB",
        "ALLSD",
        "ALLSE",
        "ALLVD",
        "ALLWK",
        "ETOTAL",
    ]

    def __init__(
        self,
        name,
        fem_set,
        set_type,
        variables,
        int_value=1,
        int_type="frequency",
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._fem_set = fem_set
        self._set_type = set_type
        self._variables = variables
        self._int_value = int_value
        self._int_type = int_type

    @property
    def type(self):
        return self._set_type

    @property
    def fem_set(self):
        return self._fem_set

    @property
    def variables(self):
        return self._variables

    @property
    def int_type(self):
        return self._int_type

    @property
    def int_value(self):
        return self._int_value


class FieldOutput(FemBase):
    """
    https://abaqus-docs.mit.edu/2017/English/SIMACAEKEYRefMap/simakey-r-output.htm

    :param name:
    :param nodal:
    :param element:
    :param contact:
    :param int_value: Field output step interval. Default is 1
    :param int_type:
    :param metadata:
    :param parent:
    """

    _valid_fstep_type = ["FREQUENCY", "NUMBER INTERVAL"]
    default_no = ["A", "CF", "RF", "U", "V"]
    default_el = ["LE", "PE", "PEEQ", "PEMAG", "S"]
    default_co = ["CSTRESS", "CDISP", "CFORCE", "CSTATUS"]

    def __init__(
        self,
        name,
        nodal=None,
        element=None,
        contact=None,
        int_value=1,
        int_type="frequency",
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._nodal = FieldOutput.default_no if nodal is None else nodal
        self._element = FieldOutput.default_el if element is None else element
        self._contact = FieldOutput.default_co if contact is None else contact
        self._int_value = int_value
        self._int_type = int_type

    def edit(self, parent=None, nodal=None, element=None, contact=None):
        """

        :param parent:
        :param nodal:
        :param element:
        :param contact:
        :return:
        """
        self._parent = parent if parent is not None else self._parent
        self._nodal = nodal if nodal is not None else self._nodal
        self._element = element if element is not None else self._element
        self._contact = contact if contact is not None else self._contact

    @property
    def nodal(self):
        return self._nodal

    @property
    def element(self):
        return self._element

    @property
    def contact(self):
        return self._contact

    @property
    def int_value(self):
        return self._int_value

    @int_value.setter
    def int_value(self, value):
        if value < 0:
            raise ValueError("The interval or frequency value cannot be less than 0")

        self._int_value = value

    @property
    def int_type(self):
        return self._int_type.upper()

    @int_type.setter
    def int_type(self, value):
        if value.upper() not in FieldOutput._valid_fstep_type:
            raise ValueError(f'Field output step type "{value}" is not supported')
        self._int_type = value.upper()


class Step(FemBase):
    """
    A FEM analysis step object

    :param name: Name of step
    :param step_type: Step type: | 'static' | 'eigenfrequency' |  'response_analysis' | 'dynamic' | 'complex_eig' |
    :param nl_geom: Include or ignore the nonlinear effects of large deformations and displacements (default=False)
    :param total_incr: Maximum number of allowed increments
    :param init_incr: Initial increment
    :param total_time: Total step time
    :param min_incr: Minimum allowable increment size
    :param max_incr: Maximum allowable increment size
    :param unsymm: Unsymmetric Matrix storage (default=False)
    :param stabilize: Default=None.
    :param dyn_type: Dynamic analysis type 'TRANSIENT FIDELITY' | 'QUASI-STATIC'
    :param init_accel_calc: Initial acceleration calculation
    :param eigenmodes: Eigenmodes
    :param alpha: Rayleigh Damping for use in Steady State analysis
    :param beta: Rayleigh Damping for use in Steady State analysis
    :param nodeid: Node ID for use in Steady State analysis
    :param fmin: Minimum frequency for use in Steady State analysis
    :param fmax: Maximum frequency for use in Steady State analysis

    :type name: str
    :type step_type: str
    :type nl_geom: bool
    :type total_incr: int
    :type init_incr: float
    :type total_time: float
    :type min_incr: float
    :type max_incr: float
    :type unsymm: bool
    :type stabilize: dict
    :type dyn_type: str
    :type init_accel_calc: bool
    :type eigenmodes: int
    :type alpha: float
    :type beta: float
    :type nodeid: int
    :type fmin: float
    :type fmax: float

    """

    _valid_steps = [
        "static",
        "eigenfrequency",
        "response_analysis",
        "dynamic",
        "complex_eig",
        "explicit",
    ]
    _valid_dyn_type = ["QUASI-STATIC", "TRANSIENT FIDELITY"]

    default_hist = HistOutput("default_hist", None, "energy", HistOutput.default_hist)
    default_field = FieldOutput("default_fields", int_type="FREQUENCY", int_value=1)

    def __init__(
        self,
        name,
        step_type,
        total_time=None,
        nl_geom=False,
        total_incr=1000,
        init_incr=100.0,
        min_incr=1e-8,
        max_incr=100.0,
        unsymm=False,
        stabilize=None,
        dyn_type="QUASI-STATIC",
        init_accel_calc=True,
        eigenmodes=20,
        alpha=0.1,
        beta=10,
        nodeid=None,
        fmin=0,
        restart_int=None,
        fmax=10,
        visco=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        if step_type not in self._valid_steps:
            raise ValueError(f'Step type "{step_type}" is currently not supported')
        if total_time is not None:
            if init_incr > total_time and step_type != "explicit" and nl_geom is True:
                raise ValueError(f"Initial increment ({init_incr}) must be smaller than total time ({total_time})")
        else:
            total_time = init_incr
        if dyn_type not in Step._valid_dyn_type:
            raise ValueError(f'Dynamic input type "{dyn_type}" is not supported')

        self._restart_int = restart_int
        self._step_type = step_type
        self._nl_geom = nl_geom
        self._total_incr = total_incr
        self._init_incr = init_incr
        self._total_time = total_time
        self._min_incr = min_incr
        self._max_incr = max_incr
        self._unsymm = unsymm
        self._stabilize = stabilize
        self._dyn_type = dyn_type
        self._init_accel_calc = init_accel_calc
        self._eigenmodes = eigenmodes
        self._alpha = alpha
        self._beta = beta
        self._nodeid = nodeid
        self._fmin = fmin
        self._fmax = fmax
        self._visco = visco

        # Not-initialized parameters
        self._bcs = dict()
        self._loads = list()
        self._interactions = dict()
        self._hist_outputs = [self.default_hist]
        self._field_outputs = [self.default_field]

    def add_load(self, load):
        """

        :param load:
        :type load: Load
        """
        self._loads.append(load)

    def add_bc(self, bc):
        """
        Adds a BC move

        :param bc: Boundary condition object
        :type bc: ada.fem.Bc

        """
        bc.parent = self
        self._bcs[bc.name] = bc

        if bc.fem_set not in self.parent.sets:
            self.parent.sets.add(bc.fem_set)

    def add_history_output(self, hist_output):
        """
        Adds history output requests

        :param hist_output: Unique History Output
        :type hist_output: HistOutput
        """
        self._hist_outputs.append(hist_output)

    def add_field_output(self, field_output):
        """
        Adds field output requests

        :param field_output: Unique field output
        :type field_output: FieldOutput

        """
        self._field_outputs.append(field_output)

    def add_interaction(self, interaction):
        """

        :param interaction:
        :type interaction: Interaction
        :return:
        """
        interaction.parent = self
        self._interactions[interaction.name] = interaction

    @property
    def type(self):
        return self._step_type

    @property
    def nl_geom(self):
        return self._nl_geom

    @property
    def total_incr(self):
        return self._total_incr

    @property
    def init_incr(self):
        return self._init_incr

    @property
    def total_time(self):
        return self._total_time

    @property
    def min_incr(self):
        return self._min_incr

    @property
    def max_incr(self):
        return self._max_incr

    @property
    def unsymm(self):
        return self._unsymm

    @property
    def stabilize(self):
        """

        :return:
        """
        return self._stabilize

    @property
    def dyn_type(self):
        return self._dyn_type

    @property
    def init_accel_calc(self):
        return self._init_accel_calc

    @property
    def eigenmodes(self):
        return self._eigenmodes

    @property
    def alpha(self):
        return self._alpha

    @property
    def beta(self):
        return self._beta

    @property
    def nodeid(self):
        return self._nodeid

    @property
    def fmin(self):
        return self._fmin

    @property
    def fmax(self):
        return self._fmax

    @property
    def visco(self):
        return self._visco

    @property
    def interactions(self):
        return self._interactions

    @property
    def bcs(self):
        return self._bcs

    @property
    def restart_int(self):
        """

        :return: Restart request intervals
        """
        return self._restart_int

    @property
    def loads(self):
        return self._loads

    @property
    def field_outputs(self):
        return self._field_outputs

    @property
    def hist_outputs(self):
        return self._hist_outputs


class LoadCase(FemBase):
    """
    The base LoadCase object. Defaults lc ids refer to the dictionary of basic loadcases used in ULS analysis.
    The dict can be changed using add_basic_lc function, but must be same as Genie lc FEMid.

    :param name:
    :param comment:
    :param loads:
    :param mass:
    :param lcsys:
    :param metadata:
    :param parent:
    """

    def __init__(
        self,
        name,
        comment,
        loads=None,
        mass=None,
        lcsys=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self._comment = comment
        self._loads = loads
        self._mass = mass
        self._lcsys = lcsys

    @property
    def loads(self):
        return self._loads

    @property
    def mass(self):
        return self._mass

    @property
    def comment(self):
        return self._comment

    @property
    def csys(self):
        return (1, 0, 0), (0, 1, 0), (0, 0, 1) if self._lcsys is None else self._lcsys

    def __repr__(self):
        return f"LC({self.name}, {self.comment})"


class Load(FemBase):
    TYPES = ["gravity", "acc", "acc_rot", "force", "force_set", "mass"]

    """


    :param load_type: Type of loads. See Load.TYPES for allowable load types.
    :param magnitude: Magnitude of load
    :param name: (Required in the event of a point load being applied)
    :param fem_set: Set reference (Required in the event of a point load being applied)
    :param dof: Degrees of freedom (Required in the event of a point load being applied)
    :param follower_force: Should follower force be accounted for
    :param amplitude: Attach an amplitude object to the load
    :param accr_origin: Origin of a rotational Acceleration field (necessary for load_type='acc_rot').
    :type load_type: str
    :type magnitude: float
    :type dof: list
    :type fem_set: FemSet
    """

    def __init__(
        self,
        name,
        load_type,
        magnitude,
        fem_set=None,
        dof=None,
        amplitude=None,
        follower_force=False,
        acc_vector=None,
        accr_origin=None,
        accr_rot_axis=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        self.type = load_type
        self._magnitude = magnitude
        self._fem_set = fem_set
        self._dof = dof
        self._amplitude = amplitude
        self._follower_force = follower_force
        self._acc_vector = acc_vector
        self._accr_origin = accr_origin
        self._accr_rot_axis = accr_rot_axis
        if self.type == "point_load":
            if self._dof is None or self._fem_set is None or self._name is None:
                raise Exception("self._dofs and nid (Node id) and name needs to be set in order to use point loads")
            if len(self._dof) != 6:
                raise Exception(
                    "You need to include all 6 dofs even though forces are not applied in all 6 dofs. "
                    "Use None or 0.0 for the dofs not applied with forces"
                )

    @property
    def type(self):
        return self._type

    @type.setter
    def type(self, value):
        if value.lower() not in self.TYPES:
            raise ValueError(f'Load type "{value}" is not yet supported or does not exist. Must be "{self.TYPES}"')
        self._type = value

    @property
    def dof(self):
        if self._dof is None and self.type is co.GRAVITY:
            self._dof = [0, 0, 1] if self._dof is None else self._dof
        return self._dof

    @property
    def amplitude(self):
        return self._amplitude

    @property
    def magnitude(self):
        return self._magnitude

    @property
    def fem_set(self):
        return self._fem_set

    @property
    def follower_force(self):
        return self._follower_force

    @property
    def acc_vector(self):
        if self.type not in ("acc", "acc_rot"):
            raise ValueError('Acceleration vector only applies for type "acc"')

        dir_error = "If acc_vector is not specified, you must pass dof=[int] (int 1-3) for the acc field"

        if self._acc_vector is not None:
            return self._acc_vector
        else:
            if len(self._dof) != 1:
                raise ValueError(dir_error)
            acc_dir = self._dof[0]
            if 1 > acc_dir > 3:
                raise ValueError(dir_error)

            if acc_dir == 1:
                dvec = 1, 0, 0
            elif acc_dir == 2:
                dvec = 0, 1, 0
            else:
                dvec = 0, 0, 1

            return tuple([float(self._magnitude * d) if d != 0 else 0.0 for d in dvec])

    @property
    def acc_rot_origin(self):
        return self._accr_origin

    @acc_rot_origin.setter
    def acc_rot_origin(self, value):
        self._accr_origin = value

    @property
    def acc_rot_axis(self):
        return self._accr_rot_axis
