from __future__ import annotations

import logging
from bisect import bisect_left
from dataclasses import dataclass
from itertools import chain, groupby
from operator import attrgetter
from typing import Iterable, List

import numpy as np

from ada.concepts.points import Node
from ada.concepts.structural import Beam
from ada.config import Settings

from .common import Amplitude, Csys, FemBase
from .constraints import Bc, Constraint
from .elements import Connector, ConnectorSection, Elem, FemSection, Mass
from .interactions import Interaction, InteractionProperty
from .sets import FemSet
from .steps import Step
from .surfaces import Surface


@dataclass
class COG:
    p: np.array
    tot_mass: float
    tot_vol: float
    sh_mass: float
    bm_mass: float
    no_mass: float


class FEM(FemBase):
    """
    A FEM representation of its parent Part

    :param name: Name of analysis model
    :param parent: Part object
    :param metadata: Attached metadata
    :type parent: ada.Part
    """

    def __init__(self, name=None, parent=None, metadata=None):
        from ada.concepts.containers import Nodes

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

    def add_elem(self, elem: Elem):
        elem.parent = self
        self.elements.add(elem)

    def add_section(self, section: FemSection):
        section.parent = self
        self.sections.add(section)

    def add_bc(self, bc: Bc):
        if bc.name in [b.name for b in self._bcs]:
            raise Exception('BC with name "{bc_id}" already exists'.format(bc_id=bc.name))
        bc.parent = self
        if bc.fem_set.parent is None:
            # TODO: look over this implementation. Is this okay?
            logging.error("Bc FemSet has no parent. Adding to self")
            self.sets.add(bc.fem_set)

        self._bcs.append(bc)

    def add_mass(self, mass: Mass):
        mass.parent = self
        self._masses[mass.name] = mass

    def add_set(
        self,
        fem_set: FemSet,
        ids=None,
        p=None,
        vol_box=None,
        vol_cyl=None,
        single_member=False,
        tol=1e-4,
    ) -> FemSet:
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

    def add_step(self, step: Step) -> Step:
        """Add an analysis step to the assembly"""
        if len(self._steps) > 0:
            if self._steps[-1].type != "eigenfrequency" and step.type == "complex_eig":
                raise Exception(
                    "complex eigenfrequency analysis step needs to follow eigenfrequency step. Check your input"
                )
        step.parent = self
        self._steps.append(step)

        return step

    def add_interaction_property(self, int_prop: InteractionProperty) -> InteractionProperty:
        int_prop.parent = self
        self._intprops[int_prop.name] = int_prop
        return int_prop

    def add_interaction(self, interaction: Interaction) -> Interaction:
        interaction.parent = self
        self._interactions[interaction.name] = interaction
        return interaction

    def add_constraint(self, constraint: Constraint) -> Constraint:
        constraint.parent = self
        self._constraints.append(constraint)
        return constraint

    def add_lcsys(self, lcsys: Csys) -> Csys:
        if lcsys.name in self._lcsys.keys():
            raise ValueError("Local Coordinate system cannot have duplicate name")
        lcsys.parent = self
        self._lcsys[lcsys.name] = lcsys
        return lcsys

    def add_connector_section(self, connector_section: ConnectorSection):
        connector_section.parent = self
        self._connector_sections[connector_section.name] = connector_section

    def add_connector(self, connector: Connector):
        connector.parent = self
        self._connectors[connector.name] = connector
        connector.csys.parent = self
        self.elements.add(connector)
        self.add_set(FemSet(name=connector.name, members=[connector.id], set_type="elset"))

    def add_rp(self, name, node: Node):
        """Adds a reference point in assembly with a specific name"""
        node.parent = self
        self.nodes.add(node)
        fem_set = self.add_set(FemSet(name, [node], "nset"))
        return node, fem_set

    def add_surface(self, surface: Surface):
        surface.parent = self
        self._surfaces[surface.name] = surface

    def add_amplitude(self, amplitude: Amplitude):
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
        tol = Settings.point_tol

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
                if elem.fem_sec.offset is not None:
                    if n in [x[0] for x in elem.fem_sec.offset]:
                        elem.fem_sec.offset[i] = (n2, elem.fem_sec.offset[i][1])

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

    def create_fem_elem_from_obj(self, obj, el_type=None) -> Elem:
        """Converts structural object to FEM elements. Currently only BEAM is supported"""

        if type(obj) is not Beam:
            raise NotImplementedError(f'Object type "{type(obj)}" is not yet supported')

        el_type = "B31" if el_type is None else el_type

        res = self.nodes.add(obj.n1)
        if res is not None:
            obj.n1 = res
        res = self.nodes.add(obj.n2)
        if res is not None:
            obj.n2 = res

        elem = Elem(None, [obj.n1, obj.n2], el_type)
        self.add_elem(elem)
        femset = FemSet(f"{obj.name}_set", [elem.id], "elset")
        self.add_set(femset)
        self.add_section(
            FemSection(
                f"d{obj.name}_sec",
                "beam",
                femset,
                obj.material,
                obj.section,
                obj.ori[1],
            )
        )
        return elem

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

        :rtype: ada.concepts.containers.Nodes
        """
        return self._nodes

    @property
    def elements(self) -> FemElements:
        return self._elements

    @property
    def sections(self) -> FemSections:
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

    @initial_state.setter
    def initial_state(self, value):
        self._initial_state = value

    @property
    def springs(self):
        return self._springs

    @property
    def lcsys(self):
        return self._lcsys

    def __add__(self, other: FEM):
        # Nodes
        nodid_max = self.nodes.max_nid if len(self.nodes) > 0 else 0
        if nodid_max > other.nodes.min_nid:
            other.nodes.renumber(int(nodid_max + 10))

        self._nodes += other.nodes

        # Elements
        elid_max = self.elements.max_el_id if len(self.elements) > 0 else 0

        if elid_max > other.elements.min_el_id:
            other.elements.renumber(int(elid_max + 10))

        logging.info("FEM operand type += is still ")

        self._elements += other.elements
        self._sections += other._sections
        self._sets += other._sets
        self._lcsys.update(other.lcsys)

        return self

    def __repr__(self):
        return f"FEM({self.name}, Elements: {len(self.elements)}, Nodes: {len(self.nodes)})"


class FemElements:
    """

    :param elements:
    :param fem_obj:
    :type fem_obj: ada.fem.FEM
    """

    def __init__(self, elements=None, fem_obj=None, from_np_array=None):
        self._fem_obj = fem_obj
        if from_np_array is not None:
            elements = self.elements_from_array(from_np_array)

        self._elements = list(sorted(elements, key=attrgetter("id"))) if elements is not None else []
        self._idmap = {e.id: e for e in self._elements} if len(self._elements) > 0 else dict()

        if len(self._idmap) != len(self._elements):
            raise ValueError("Unequal length of idmap and elements. Might indicate doubly defined element id's")

        self._group_by_types()

    def renumber(self, start_id=1):
        from ada.core.utils import Counter

        elid = Counter(start_id)

        def mapid2(el: Elem):
            el.id = next(elid)

        list(map(mapid2, self._elements))
        self._idmap = {e.id: e for e in self._elements} if len(self._elements) > 0 else dict()

        self._group_by_types()

    def link_nodes(self):
        """
        Link element nodes with the parent fem node collection

        """

        def grab_nodes(elem):
            """

            :param elem:
            :type elem: ada.fem.Elem
            """

            nodes = [self._fem_obj.nodes.from_id(no) for no in elem.nodes if type(no) in (int, np.int32)]
            if len(nodes) != len(elem.nodes):
                raise ValueError("Unable to convert element nodes")
            elem._nodes = nodes

        list(map(grab_nodes, self._elements))

    def _build_sets_from_elsets(self, elset, elem_iter):
        from ..fem import FemSet

        if elset is not None and type(elset) == str:

            def link_elset(elem, elem_set_):
                elem._elset = elem_set_

            elements = [self.from_id(el.id) for el in elem_iter]
            if elset not in self._fem_obj.elsets:
                elem_set = FemSet(elset, elements, "elset", parent=self._fem_obj)
                self._fem_obj.sets.add(elem_set)
                list(map(lambda x: link_elset(x, elem_set), elements))
            else:
                self._fem_obj.elsets[elset].add_members(elements)
                elem_set = self._fem_obj.elsets[elset]
                list(map(lambda x: link_elset(x, elem_set), elements))

    def elements_from_array(self, array):
        """

        :param array: A list of numpy arrays formatted as [[elid, n1, n2,...,ni, elset, eltype], ..]
        :return:
        """
        from ada.fem import Elem

        def to_elem(e):
            nodes = [self._fem_obj.nodes.from_id(n) for n in e[3:] if (n == 0 or np.isnan(n)) is False]
            return Elem(e[0], nodes, e[1], e[2], parent=self._fem_obj)

        return list(map(to_elem, array))

    def build_sets(self):
        """
        Create sets from attached elset attribute on elements
        """
        for elset, elements in groupby(self._elements, key=attrgetter("elset")):
            if elset is None:
                continue
            self._build_sets_from_elsets(elset, elements)

    def remove_elements_by_set(self, elset):
        """
        Remove elements from element set. Will remove element set on completion

        :param elset: Pass in a
        :type elset: ada.fem.FemSet
        """
        for el in elset.members:
            self.remove(el)
        self._sort()
        p = elset.parent
        p.sets.elements.pop(elset.name)

    def remove_elements_by_id(self, ids):
        """
        Remove elements from element ids. Will remove elements on completion

        :param ids: Pass in a
        :type ids: int or list of ints
        """
        from collections.abc import Iterable

        ids = list(ids) if isinstance(ids, Iterable) else [ids]
        for elem_id in ids:
            self.remove(self._idmap[elem_id])
        self._sort()

    def __contains__(self, item):
        return item in self._elements

    def __len__(self):
        return len(self._elements)

    def __iter__(self):
        return iter(self._elements)

    def __getitem__(self, index):
        result = self._elements[index]
        return FemElements(result) if isinstance(index, slice) else result

    def __add__(self, other):
        return FemElements(chain.from_iterable([self.elements, other.elements]), self._fem_obj)

    def __repr__(self):
        data = {}
        for key, val in groupby(sorted(self._elements, key=attrgetter("type")), key=attrgetter("type")):
            if key not in data.keys():
                data[key] = len(list(val))
            else:
                data[key] += len(list(val))
        data_str = ", ".join([f'"{key}": {val}' for key, val in data.items()])
        return f"FemElementsCollection(Elements: {len(self._elements)}, By Type: {data_str})"

    def by_types(self):
        return self._by_types

    def calc_cog(self):
        """
        Calculate COG, total mass and structural Volume of your FEM model based on element mass distributed to element
        nodes

        :return: cogx, cogy, cogz, tot_mass, tot_vol
        :rtype: COG
        """
        from itertools import chain

        from ada.core.utils import (
            global_2_local_nodes,
            normal_to_points_in_plane,
            poly_area,
            unit_vector,
            vector_length,
        )

        def calc_sh_elem(el):
            """

            :param el:
            :type el: ada.Elem
            """
            locz = el.fem_sec.local_z if el.fem_sec.local_z is not None else normal_to_points_in_plane(el.nodes)
            locx = unit_vector(el.nodes[1].p - el.nodes[0].p)
            locy = np.cross(locz, locx)
            origin = el.nodes[0]
            t = el.fem_sec.thickness

            ln = global_2_local_nodes([locx, locy], origin, el.nodes)
            x, y, z = list(zip(*ln))
            area = poly_area(x, y)
            vol_ = t * area
            mass = vol_ * el.fem_sec.material.model.rho
            center = sum([e.p for e in el.nodes]) / len(el.nodes)

            return mass, center, vol_

        def calc_bm_elem(el):
            """

            :param el:
            :type el: ada.Elem
            """
            el.fem_sec.section.properties.calculate()
            nodes_ = el.fem_sec.get_offset_coords()
            elem_len = vector_length(nodes_[-1] - nodes_[0])
            vol_ = el.fem_sec.section.properties.Ax * elem_len
            mass = vol_ * el.fem_sec.material.model.rho
            center = sum([e.p for e in el.nodes]) / len(el.nodes)

            return mass, center, vol_

        def calc_mass_elem(el):
            """

            :param el:
            :type el: ada.Elem
            """
            if el.mass_props.type != "MASS":
                raise NotImplementedError(f'Mass type "{el.mass_props.type}" is not yet implemented')
            mass = el.mass_props.mass
            vol_ = 0.0
            return mass, el.nodes[0].p, vol_

        sh = list(chain(map(calc_sh_elem, self.shell)))
        bm = list(chain(map(calc_bm_elem, self.lines)))
        ma = list(chain(map(calc_mass_elem, self.masses)))

        tot_mass = 0.0
        tot_vol = 0.0
        mcog_ = np.array([0, 0, 0]).astype(float)

        sh_mass = sum([r[0] for r in sh])
        bm_mass = sum([r[0] for r in bm])
        no_mass = sum([r[0] for r in ma])

        for m, c, vol in sh + bm + ma:
            tot_vol += vol
            tot_mass += m
            mcog_ += m * np.array(c).astype(float)

        cog_ = mcog_ / tot_mass

        return COG(cog_, tot_mass, tot_vol, sh_mass, bm_mass, no_mass)

    @property
    def max_el_id(self):
        return max(self._idmap.keys())

    @property
    def min_el_id(self):
        return min(self._idmap.keys())

    @property
    def elements(self):
        return self._elements

    @property
    def solids(self):
        from ada.fem.shapes import ElemShapes

        skipel = ["MASS", "SPRING1"]
        return filter(lambda x: x.type not in skipel and x.type in ElemShapes.volume, self._elements)

    @property
    def shell(self):
        from ada.fem.shapes import ElemShapes

        skipel = ["MASS", "SPRING1"]
        return filter(lambda x: x.type not in skipel and x.type in ElemShapes.shell, self._elements)

    @property
    def lines(self):
        from ada.fem.shapes import ElemShapes

        skipel = ["MASS", "SPRING1"]
        return filter(lambda x: x.type not in skipel and x.type in ElemShapes.lines, self._elements)

    @property
    def connectors(self):
        """

        :return: Connector elements (lazy iterator)
        """
        from ada.fem.shapes import ElemShapes

        skipel = ["MASS", "SPRING1"]
        return filter(lambda x: x.type not in skipel and x.type in ElemShapes.connectors, self._elements)

    @property
    def masses(self):
        """

        :return: Mass elements (lazy iterator)
        """
        from ada.fem import Mass

        return filter(lambda x: x.type in Mass._valid_types, self._elements)

    @property
    def stru_elements(self) -> Iterable[Elem]:
        return filter(lambda x: x.type not in ["MASS", "SPRING1"], self._elements)

    def from_id(self, el_id):
        """

        :param el_id:
        :rtype: ada.fem.Elem
        """
        if el_id not in self._idmap.keys():
            raise ValueError(f'The elem id "{el_id}" is not found')
        else:
            return self._idmap[el_id]

    def filter_elements(self, keep_elem=None, delete_elem=None):
        """
        Filter which element types you wish to keep using the "keep_elem" list of elem_types or which elements you wish
        to delete using the 'delete_elem' option.

        :param keep_elem:
        :param delete_elem:
        """
        keep_elem = [el_.lower() for el_ in keep_elem] if keep_elem is not None else None
        delete_elem = [el_.lower() for el_ in delete_elem] if delete_elem is not None else None

        def eval_elem(el):
            if keep_elem is not None:
                return True if el.type.lower() in keep_elem else False
            else:
                return False if el.type.lower() in delete_elem else True

        self._elements = list(filter(eval_elem, self._elements))
        self._by_types = dict(self.group_by_type())
        self._idmap = {e.id: e for e in self._elements}

    @property
    def idmap(self):
        """

        :return:
        """
        return self._idmap

    def add(self, elem):
        """

        :param elem:
        :type elem: ada.fem.Elem
        :return:
        """
        if elem.id is None:
            if len(self._elements) > 0:
                elem._el_id = self._elements[-1].id + 1
            else:
                elem._el_id = 1
        if elem.id in self.idmap.keys():
            raise ValueError(f'Elem id "{elem.id}" already exists or is not set.')

        if elem.parent is None:
            elem.parent = self._fem_obj

        self._elements.append(elem)
        self._idmap[elem.id] = elem

        self._group_by_types()

    def remove(self, elems):
        """
        Remove node from the nodes container
        :param elems: Element-object to be removed
        :type elems:ada.fem.Elem or List[ada.fem.Elem]
        :return:
        """
        from collections.abc import Iterable

        elems = list(elems) if isinstance(elems, Iterable) else [elems]
        for elem in elems:
            if elem in self._elements:
                logging.error(f"Removing element {elem}")
                self._elements.pop(self._elements.index(elem))
            else:
                logging.error(f"'{elem}' not found in {self.__class__.__name__}-container.")
        self._sort()

    def group_by_type(self):
        return groupby(sorted(self._elements, key=attrgetter("type")), key=attrgetter("type"))

    def _group_by_types(self):
        if len(self._elements) > 0:
            self._by_types = groupby(self._elements, key=attrgetter("type", "elset"))
        else:
            self._by_types = dict()

    def _sort(self):
        self._elements = sorted(self._elements, key=attrgetter("id"))
        self._group_by_types()
        self.renumber()

    def merge_with_coincident_nodes(self):
        def remove_duplicate_nodes():
            new_nodes = [n for n in elem.nodes if len(n.refs) > 0]
            elem.nodes.clear()
            elem.nodes.extend(new_nodes)

        """
        This does not work according to plan. It seems like it is deleting more and more from the model for each
        iteration
        """
        for elem in filter(lambda x: len(x.nodes) > len([n for n in x.nodes if len(n.refs) > 0]), self._elements):
            remove_duplicate_nodes()
            elem.update()

    def update(self):
        self.remove(list(filter(lambda x: x.id is None, self._elements)))


class FemSections:
    """

    :param sections:
    :param fem_obj:
    :type fem_obj: ada.fem.FEM
    """

    def __init__(self, sections=None, fem_obj=None):
        self._fem_obj = fem_obj
        self._sections = list(sections) if sections is not None else []
        by_types = self._groupby()
        self._lines = by_types["lines"]
        self._shells = by_types["shells"]
        self._solids = by_types["solids"]
        self._dmap = {e.name: e for e in self._sections} if len(self._sections) > 0 else dict()

        if len(self._sections) > 0 and fem_obj is not None:
            self._link_data()

    def _map_materials(self, fem_sec, mat_repo):
        """

        :param fem_sec:
        :type fem_sec: ada.fem.FemSection
        :param mat_repo:
        :type mat_repo: ada.core.containers.Materials
        """

        if type(fem_sec.material) is str:
            fem_sec._material = mat_repo.get_by_name(fem_sec.material)

    def _map_elsets(self, fem_sec, elset_repo):
        """

        :param fem_sec:
        :type fem_sec: ada.fem.FemSection
        """
        from ada.fem import FemSection

        if type(fem_sec.elset) is str:
            if fem_sec.elset not in elset_repo:
                raise ValueError(f'The element set "{fem_sec.elset}" is not imported. ')
            fem_sec._elset = elset_repo[fem_sec.elset]
        elif type(fem_sec) is FemSection:
            pass
        else:
            raise ValueError("Invalid element set has been attached to this fem section")

    def _link_data(self):
        from functools import partial

        mat_repo = self._fem_obj.parent.get_assembly().materials
        list(map(partial(self._map_materials, mat_repo=mat_repo), self._sections))

        elsets_repo = self._fem_obj.elsets
        list(map(partial(self._map_elsets, elset_repo=elsets_repo), self._sections))
        [fem_sec.link_elements() for fem_sec in self._sections]

    def _groupby(self):
        return dict(
            lines=list(filter(lambda x: x.type == "lines", self._sections)),
            shells=list(filter(lambda x: x.type == "shell", self._sections)),
            solids=list(filter(lambda x: x.type == "solid", self._sections)),
        )

    def __contains__(self, item):
        """

        :param item:
        :type item: ada.fem.FemSection
        """
        return item in self._sections

    def __len__(self):
        return len(self._sections)

    def __iter__(self):
        return iter(self._sections)

    def __getitem__(self, index):
        result = self._sections[index]
        return FemSections(result) if isinstance(index, slice) else result

    def __add__(self, other: FemSections):
        return FemSections(chain(self._sections, other.sections))

    def __repr__(self):
        return f"FemSections(Beams: {len(self.lines)}, Shells: {len(self.shells)}, Solids: {len(self.solids)})"

    @property
    def sections(self) -> List[FemSection]:
        return self._sections

    @property
    def lines(self):
        return self._lines

    @property
    def shells(self):
        return self._shells

    @property
    def solids(self):
        return self._solids

    def edges(self):
        return None

    @property
    def dmap(self):
        return self._dmap

    def index(self, item):
        index = bisect_left(self._sections, item)
        if (index != len(self._sections)) and (self._sections[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def _map_femsec_to_elem(self, elem, fem_sec):
        """

        :param elem:
        :type elem: ada.fem.Elem
        """
        elem.fem_sec = fem_sec

    def add(self, sec):
        """

        :param sec:
        :type sec: ada.fem.FemSection
        :return:
        """
        from functools import partial

        if sec.name in self.dmap.keys() or sec.name is None:
            raise ValueError(f'Section name "{sec.name}" already exists')
            # sec._name = sec.name+'_1'

        self._sections.append(sec)
        if sec.type == "line":
            self._lines.append(sec)
        elif sec.type == "shell":
            self._shells.append(sec)
        else:
            self._solids.append(sec)

        list(map(partial(self._map_femsec_to_elem, fem_sec=sec), sec.elset.members))
        self._dmap[sec.name] = sec


class FemSets:
    """

    :param sets:
    :param fem_obj:
    :type fem_obj: ada.fem.FEM
    """

    def __init__(self, sets=None, fem_obj=None):
        self._fem_obj = fem_obj
        self._sets = sorted(sets, key=attrgetter("type", "name")) if sets is not None else []
        # Merge same name sets
        self._nomap = self._assemble_sets(self.is_nset) if len(self._sets) > 0 else dict()
        self._elmap = self._assemble_sets(self.is_elset) if len(self._sets) > 0 else dict()
        self._same_names = dict()
        if len(self._sets) > 0:
            self.link_data()

    def add_references(self):
        """
        Add reference to the containing FemSet for each member (node or element)

        :return:
        """

        def _map_ref(el, fem_set):
            el.refs.append(fem_set)

        for _set in self._sets:
            [_map_ref(m, _set) for m in _set.members]

    @staticmethod
    def is_nset(fs):
        return True if fs.type == "nset" else False

    @staticmethod
    def is_elset(fs):
        return True if fs.type == "elset" else False

    def _instantiate_all_members(self, fem_set):
        """

        :param fem_set:
        :type fem_set: ada.fem.FemSet
        """
        from ada import Node
        from ada.fem import Elem

        def get_nset(nref):
            if type(nref) is Node:
                return nref
            else:
                return fem_set.parent.nodes.from_id(nref)

        def get_elset(elref):
            if type(elref) in (int, np.int32):
                return fem_set.parent.elements.from_id(elref)
            elif type(elref) is Elem:
                if elref not in elref.parent.elements and len(elref.parent.elements) != 0:
                    raise ValueError("Element might be doubly defined")
                else:
                    return elref
            else:
                raise ValueError("Elref is not recognized")

        def eval_set(fset):
            if fset.type == "elset":
                el_type = Elem
                get_func = get_elset
            else:
                el_type = Node
                get_func = get_nset

            res = list(filter(lambda x: type(x) != el_type, fset.members))
            if len(res) > 0:
                fset._members = [get_func(m) for m in fset.members]

        if "generate" in fem_set.metadata.keys():
            if fem_set.metadata["generate"] is True and len(fem_set.members) == 0:
                gen_mem = fem_set.metadata["gen_mem"]
                fem_set._members = [i for i in range(gen_mem[0], gen_mem[1] + 1, gen_mem[2])]
                fem_set.metadata["generate"] = False

        if fem_set.type == "nset":
            if len(fem_set.members) == 1 and type(fem_set.members[0]) is str and type(fem_set.members[0]) is not Node:
                fem_set._members = self.nodes[fem_set.members[0]]
                fem_set.parent = self._fem_obj
                return fem_set

        eval_set(fem_set)
        fem_set.parent = self._fem_obj
        return fem_set

    def link_data(self):
        list(map(self._instantiate_all_members, self._sets))

    def _assemble_sets(self, set_type):
        elsets = dict()
        for elset in filter(set_type, self._sets):
            if elset.name not in elsets.keys():
                elsets[elset.name] = elset
            else:
                self._instantiate_all_members(elset)
                elsets[elset.name] += elset
        return elsets

    def __contains__(self, item):
        return item.name in self._elmap.keys()

    def __len__(self):
        return len(self._sets)

    def __iter__(self):
        return iter(self._sets)

    def __getitem__(self, index):
        result = self._sets[index]
        return FemSets(result, fem_obj=self._fem_obj) if isinstance(index, slice) else result

    def __add__(self, other):
        """

        :param other:
        :type other: FemSets
        :return:
        """
        # TODO: make default choice for similar named sets in a global settings class
        for name, _set in other.nodes.items():
            if name in self._nomap.keys():
                raise ValueError("Duplicate node set name. Consider suppressing this error?")
            self.add(_set)
        for name, _set in other.elements.items():
            if name in self._elmap.keys():
                raise ValueError("Duplicate element set name. Consider suppressing this error?")
            self.add(_set)
        return self
        # return FemSetsCollection(chain(self.sets, other.sets), fem_obj=self._fem_obj)

    def get_elset_from_name(self, name):
        """

        :param name:
        :rtype: ada.fem.classes.Elem
        """
        if name not in self._elmap.keys():
            raise ValueError(f'The elem id "{name}" is not found')
        else:
            return self._elmap[name]

    def get_nset_from_name(self, name):
        """

        :param name:
        :rtype: ada.fem.classes.Elem
        """
        if name not in self._nomap.keys():
            raise ValueError(f'The node id "{name}" is not found')
        else:
            return self._nomap[name]

    @property
    def elements(self):
        """

        :return:
        """
        return self._elmap

    @property
    def nodes(self):
        """

        :return:
        """
        return self._nomap

    @property
    def sets(self):
        return self._sets

    def index(self, item):
        index = bisect_left(self._sets, item)
        if (index != len(self._sets)) and (self._sets[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def remove(self, fe_set):
        """

        :param fe_set:
        :type fe_set:
        """
        i = self._sets.index(fe_set)
        self._sets.pop(i)
        if fe_set.type == "nset":
            self._nomap.pop(fe_set.name)
            if fe_set.name in self._same_names.keys():
                self._same_names.pop(fe_set.name)
        else:
            self._elmap.pop(fe_set.name)
            if fe_set.name in self._same_names.keys():
                self._same_names.pop(fe_set.name)

        # To evalute if dependencies of set should be checked?
        # Against: This is a downstream object. FemSections would point to this set and remove during concatenation.

    def add(self, fe_set, append_suffix_on_exist=False):
        """

        :type fe_set: ada.fem.FemSet
        :param append_suffix_on_exist:
        """
        if fe_set.type == "nset":
            if fe_set.name in self._nomap.keys():
                fem_set = self._nomap[fe_set.name]
                new_mem = [m for m in fe_set.members if m.id not in fem_set.members]
                fem_set.add_members(new_mem)
        else:
            if fe_set.name in self._elmap.keys():
                if append_suffix_on_exist is False:
                    raise ValueError("An elements set with the same name already exists")
                if fe_set.name not in self._same_names.keys():
                    self._same_names[fe_set.name] = 1
                else:
                    self._same_names[fe_set.name] += 1

                fe_set._name = f"{fe_set.name}_{self._same_names[fe_set.name]}"

        # if False in list(map(lambda x: type(x) is Node, fe_set.members)):
        #     self._map_members(fe_set)
        self._sets.append(fe_set)
        if fe_set.type == "elset":
            self._elmap[fe_set.name] = fe_set
        else:
            self._nomap[fe_set.name] = fe_set

        if fe_set.parent is None:
            fe_set.parent = self._fem_obj

        self._instantiate_all_members(fe_set)

        return fe_set
