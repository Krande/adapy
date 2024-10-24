from __future__ import annotations

import reprlib
from bisect import bisect_left, bisect_right
from itertools import chain
from operator import attrgetter
from typing import TYPE_CHECKING, Dict, Iterable, List, Union

import numpy as np

from ada.api.beams import Beam
from ada.api.beams.helpers import get_beam_extensions
from ada.api.exceptions import DuplicateNodes
from ada.api.nodes import Node, replace_node
from ada.api.plates.base_pl import Plate
from ada.api.transforms import Rotation
from ada.base.units import Units
from ada.config import Config, logger
from ada.core.utils import Counter, roundoff
from ada.core.vector_utils import (
    is_null_vector,
    is_parallel,
    points_in_cylinder,
    unit_vector,
    vector_length,
)
from ada.materials import Material

if TYPE_CHECKING:
    from ada import FEM, Assembly, Part
    from ada.api.connections import JointBase
    from ada.fem.results.common import FemNodes
    from ada.sections import Section

__all__ = [
    "Nodes",
    "Beams",
    "Plates",
    "Connections",
    "Materials",
    "Sections",
]


class BaseCollections:
    """The Base class for all collections"""

    def __init__(self, parent: Part):
        self._parent = parent

    @property
    def parent(self) -> Part:
        return self._parent


class Beams(BaseCollections):
    """A collections of Beam objects"""

    def __init__(self, beams: Iterable[Beam] = None, parent=None):
        super().__init__(parent)
        beams = [] if beams is None else beams
        self._beams = sorted(beams, key=attrgetter("name"))
        self._nmap = {n.name: n for n in self._beams}
        self._idmap = {n.guid: n for n in self._beams}
        self._connected_beams_map = None

    def __contains__(self, item: Beam):
        return item.guid in self._idmap.keys()

    def __len__(self):
        return len(self._beams)

    def __iter__(self) -> Iterable[Beam]:
        return iter(self._beams)

    def __getitem__(self, index):
        result = self._beams[index]
        return Beams(result) if isinstance(index, slice) else result

    def __eq__(self, other):
        if not isinstance(other, Beams):
            return NotImplemented
        return self._beams == other._beams

    def __ne__(self, other):
        if not isinstance(other, Beams):
            return NotImplemented
        return self._beams != other._beams

    def __add__(self, other):
        return Beams(chain(self, other))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Beams({rpr.repr(self._beams) if self._beams else ''})"

    def merge_connected_beams_by_properties(self) -> None:
        def append_connected_beams(connected_beams: Iterable[Beam]) -> None:
            for c_beam in connected_beams:
                if c_beam not in to_be_merged:
                    to_be_merged.append(c_beam)
                    append_connected_beams(self.connected_beams_map[c_beam])

        self.set_connected_beams_map()
        merged_beams: list[Beam] = list()

        for beam in self._beams.copy():
            if beam not in merged_beams:
                to_be_merged: list[Beam] = [beam]
                append_connected_beams(self.connected_beams_map[beam])
                merged_beams.extend(to_be_merged)
                self.merge_beams(to_be_merged)

        self.set_connected_beams_map()

    def merge_beams(self, beam_segments: Iterable[Beam]) -> Beam:
        """Merge all beam segments into the first entry in beam_segments by changing the beam nodes."""
        precision = Config().general_precision

        def get_end_nodes() -> list[Node]:
            end_beams = filter(lambda x: len(self.connected_beams_map.get(x, list())) == 1, beam_segments)

            end_nds: list[Node] = list()

            for beam in end_beams:
                (node_without_connected_beam,) = self.connected_beams_map[beam]
                end_nds.append(beam.n1 if node_without_connected_beam in beam.n2.refs else beam.n2)
            return end_nds

        def modify_beam(bm: Beam, new_nodes) -> Beam:
            n1, n2 = new_nodes

            n1_2_n2_vector = unit_vector(n2.p - n1.p)
            beam_vector = bm.xvec.round(decimals=precision)

            if is_parallel(n1_2_n2_vector, bm.xvec) and not is_null_vector(n1_2_n2_vector, bm.xvec):
                n1, n2 = n2, n1
            elif not is_parallel(n1_2_n2_vector, bm.xvec):
                raise ValueError(f"Unit vector error. Beam.xvec: {beam_vector}, nodes unit_vec: {-1 * n1_2_n2_vector}")

            bm.n1, bm.n2 = n1, n2
            return bm

        if len(list(beam_segments)) > 1:
            end_nodes = get_end_nodes()
            modified_beam = modify_beam(beam_segments[0], end_nodes)

            for old_beam in beam_segments[1:]:
                self.remove(old_beam)

            return modified_beam

    def set_connected_beams_map(self) -> None:
        self._connected_beams_map = {beam: get_beam_extensions(beam) for beam in self._beams}

    @property
    def connected_beams_map(self) -> dict[Beam, Iterable[Beam]]:
        return self._connected_beams_map

    def get_beams_at_point(self, point: Union[Node, np.ndarray]) -> list[Beam]:
        return list(filter(lambda x: x.is_point_on_beam(point), self._beams))

    def index(self, item: Beam) -> int:
        index = bisect_left(self._beams, item)
        if (index != len(self._beams)) and (self._beams[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def count(self, item) -> int:
        return int(item in self)

    def from_name(self, name: str) -> Beam:
        """Get beam from its name"""
        return self._nmap.get(name)

    def from_guid(self, guid: str) -> Beam:
        """Get beam from its guid"""
        return self._idmap.get(guid)

    def add(self, beam: Beam) -> Beam:
        from .exceptions import NameIsNoneError

        if beam.name is None:
            raise NameIsNoneError("Name is not allowed to be None.")

        if beam.name in self._idmap.keys():
            logger.warning(f'Beam with name "{beam.name}" already exists. Will not add')
            return self._idmap[beam.name]

        self._idmap[beam.guid] = beam
        self._nmap[beam.name] = beam
        self._beams.append(beam)
        beam.add_beam_to_node_refs()
        return beam

    def remove(self, beam: Beam) -> None:
        beam.remove_beam_from_node_refs()
        i = self._beams.index(beam)
        self._beams.pop(i)
        self._idmap = {n.guid: n for n in self._beams}
        self._nmap = {n.name: n for n in self._beams}

    def get_beams_within_volume(self, vol_, margins) -> Iterable[Beam]:
        """
        :param vol_: List or tuple of tuples [(xmin, xmax), (ymin, ymax), (zmin, zmax)]
        :param margins: Add margins to the volume box (equal in all directions). Input is in meters. Can be negative.
        :return: List of beam ids
        """
        from bisect import bisect_left, bisect_right

        if margins is not None:
            vol_new = []
            for p in vol_:
                vol_new.append((roundoff(p[0] - margins), roundoff(p[1] + margins)))
        else:
            vol_new = vol_
        vol = vol_new

        def sort_beams(bms):
            xkeys = [key[1] for key in bms]
            xmin = bisect_left(xkeys, vol[0][0])
            xmax = bisect_right(xkeys, vol[0][1])

            within_x_list = sorted(bms[xmin:xmax], key=lambda elem: elem[2])

            ykeys = [key[2] for key in within_x_list]
            ymin = bisect_left(ykeys, vol[1][0])
            ymax = bisect_right(ykeys, vol[1][1])

            within_y_list = sorted(within_x_list[ymin:ymax], key=lambda elem: elem[3])

            zkeys = [key[3] for key in within_y_list]
            zmin = bisect_left(zkeys, vol[2][0])
            zmax = bisect_right(zkeys, vol[2][1])

            within_vol_list = within_y_list[zmin:zmax]
            return [bm[0] for bm in within_vol_list]

        bm_list1 = [(bm.name, bm.n1.x, bm.n1.y, bm.n1.z) for bm in sorted(self._beams, key=lambda bm: bm.n1.x)]
        bm_list2 = [(bm.name, bm.n2.x, bm.n2.y, bm.n2.z) for bm in sorted(self._beams, key=lambda bm: bm.n2.x)]

        return set([self.from_name(bm_id) for bms_ in (bm_list1, bm_list2) for bm_id in sort_beams(bms_)])

    @property
    def idmap(self) -> dict[str, Beam]:
        return self._idmap

    @property
    def nmap(self) -> dict[str, Beam]:
        return self._nmap


class Plates(BaseCollections):
    """Plate object collection"""

    def __init__(self, plates: Iterable[Plate] = None, parent: Part = None):
        plates = [] if plates is None else plates
        super().__init__(parent)
        self._plates = sorted(plates, key=attrgetter("name"))
        self._idmap = {n.guid: n for n in self._plates}
        self._nmap = {n.name: n for n in self._plates}

    def __contains__(self, item: Plate):
        return item.guid in self._idmap.keys()

    def __len__(self):
        return len(self._plates)

    def __iter__(self) -> Iterable[Plate]:
        return iter(self._plates)

    def __getitem__(self, index):
        result = self._plates[index]
        return Materials(result) if isinstance(index, slice) else result

    def __eq__(self, other):
        if not isinstance(other, Plates):
            return NotImplemented
        return self._plates == other._plates

    def __ne__(self, other):
        if not isinstance(other, Plates):
            return NotImplemented
        return self._plates != other._plates

    def __add__(self, other: Plates) -> Plates:
        return Plates(chain(self, other))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Plates({rpr.repr(self._plates) if self._plates else ''})"

    def index(self, plate: Plate):
        index = bisect_left(self._plates, plate)
        if (index != len(self._plates)) and (self._plates[index] == plate):
            return index
        raise ValueError(f"{repr(plate)} not found")

    def count(self, item: Plate):
        return int(item in self)

    def remove(self, plate: Plate) -> None:
        i = self._plates.index(plate)
        self._plates.pop(i)
        self._idmap = {n.guid: n for n in self._plates}
        self._nmap = {n.name: n for n in self._plates}

    def from_name(self, name: str) -> Plate:
        return self._nmap.get(name, None)

    def from_guid(self, guid: str) -> Plate:
        return self._idmap.get(guid, None)

    @property
    def idmap(self) -> dict[str, Plate]:
        return self._idmap

    @property
    def nmap(self) -> dict[str, Plate]:
        return self._nmap

    def add(self, plate: Plate) -> Plate:
        if plate.name is None:
            raise Exception("Name is not allowed to be None.")

        if plate.name in self._nmap.keys():
            return self._nmap[plate.name]
        mat = self._parent.materials.add(plate.material)
        if mat is not None:
            plate.material = mat

        self._plates.append(plate)
        self._nmap[plate.name] = plate
        self._idmap[plate.guid] = plate
        return plate


class Connections(BaseCollections):
    _counter = Counter(1, "C")

    def __init__(self, connections: Iterable[JointBase] = None, parent=None):
        connections = [] if connections is None else connections
        super().__init__(parent)
        self._connections = connections
        self._initialize_connection_data()

    def _initialize_connection_data(self):
        self._dmap = {j.name: j for j in self._connections}
        self._joint_centre_nodes = Nodes([c.centre for c in self._connections])
        self._nmap = {self._joint_centre_nodes.index(c.centre): c for c in self._connections}

    @property
    def connections(self) -> List[JointBase]:
        return self._connections

    @connections.setter
    def connections(self, value: List[JointBase]):
        self._connections = value
        self._initialize_connection_data()

    @property
    def joint_centre_nodes(self):
        return self._joint_centre_nodes

    def __contains__(self, item):
        return item.id in self._dmap.keys()

    def __len__(self):
        return len(self._connections)

    def __iter__(self) -> Iterable[JointBase]:
        return iter(self._connections)

    def __getitem__(self, index):
        result = self._connections[index]
        return Connections(result) if isinstance(index, slice) else result

    def __eq__(self, other: Connections):
        if not isinstance(other, Connections):
            return NotImplemented
        return self._connections == other._connections

    def __ne__(self, other: Connections):
        if not isinstance(other, Connections):
            return NotImplemented
        return self._connections != other._connections

    def __add__(self, other: Connections):
        return Connections(chain(self._connections, other._connections))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Connections({rpr.repr(self._connections) if self._connections else ''})"

    def get_from_name(self, name: str):
        result = self._dmap.get(name, None)
        if result is None:
            logger.error(f'No Joint with the name "{name}" found within this connection object')
        return result

    def add(self, joint: JointBase, point_tol=Config().general_point_tol):
        if joint.name is None:
            raise Exception("Name is not allowed to be None.")

        if joint.name in self._dmap.keys():
            raise ValueError("Joint Exists with same name")

        new_node = Node(joint.centre)
        node = self._joint_centre_nodes.add(new_node, point_tol=point_tol)
        if node != new_node:
            return self._nmap[node]
        else:
            self._nmap[node] = joint
        joint.parent = self
        self._dmap[joint.name] = joint
        self._connections.append(joint)

    def remove(self, joint: JointBase):
        if joint.name in self._dmap.keys():
            self._dmap.pop(joint.name)
        if joint in self._connections:
            self._connections.pop(self._connections.index(joint))
        if joint.centre in self._nmap.keys():
            self._nmap.pop(joint.centre)

    def find(self, out_of_plane_tol=0.1, joint_func=None, point_tol=Config().general_point_tol):
        """
        Find all connections between beams in all parts using a simple clash check.

        :param out_of_plane_tol:
        :param joint_func: Pass a function for mapping the generic Connection classes to a specific reinforced Joints
        :param point_tol:
        """
        from ada.api.connections import JointBase
        from ada.core.clash_check import are_beams_connected

        ass = self._parent.get_assembly()
        bm_res = ass.beam_clash_check()

        nodes = Nodes()
        nmap = dict()

        for bm1_, beams_ in bm_res:
            are_beams_connected(bm1_, beams_, out_of_plane_tol, point_tol, nodes, nmap)

        for node, mem in nmap.items():
            if joint_func is not None:
                joint = joint_func(next(self._counter), mem, node.p, parent=self)
                if joint is None:
                    continue
            else:
                joint = JointBase(next(self._counter), mem, node.p, parent=self)

            self.add(joint, point_tol=point_tol)

        print(f"Connection search finished. Found a total of {len(self._connections)} connections")


class NumericMapped(BaseCollections):
    def __init__(self, parent):
        super(NumericMapped, self).__init__(parent=parent)
        self._name_map = dict()
        self._id_map = dict()

    def recreate_name_and_id_maps(self, collection):
        self._name_map = {n.name: n for n in collection}
        self._id_map = {n.id: n for n in collection}

    @property
    def max_id(self):
        if len(self._id_map.keys()) == 0:
            return 0
        return max(self._id_map.keys())


class Materials(NumericMapped):
    """Collection of materials"""

    def __init__(self, materials: Iterable[Material] = None, parent: Union[Part, Assembly] = None, units=Units.M):
        super().__init__(parent)
        self.materials = sorted(materials, key=attrgetter("name")) if materials is not None else []
        self.recreate_name_and_id_maps(self.materials)
        self._units = units

    def __contains__(self, item: Material):
        return item.name in self._name_map.keys()

    def __len__(self) -> int:
        return len(self.materials)

    def __iter__(self) -> Iterable[Material]:
        return iter(self.materials)

    def __getitem__(self, index):
        result = self.materials[index]
        return Materials(result) if isinstance(index, slice) else result

    def __eq__(self, other: Materials):
        if not isinstance(other, Materials):
            return NotImplemented
        return self.materials == other.materials

    def __ne__(self, other: Materials):
        if not isinstance(other, Materials):
            return NotImplemented
        return self.materials != other.materials

    def __add__(self, other: Materials):
        if self.parent is None:
            raise ValueError("Parent cannot be zero")
        for mat in other:
            mat.parent = self.parent
        other.renumber_id(self.max_id + 1)
        return Materials(chain(self, other), parent=self.parent)

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Materials({rpr.repr(self.materials) if self.materials else ''})"

    def merge_materials_by_properties(self):
        models = []

        final_mats = []
        for i, mat in enumerate(self.materials):
            if mat.model.unique_props() not in models:
                models.append(mat.model.unique_props())
                final_mats.append(mat)
            else:
                index = models.index(mat.model.unique_props())
                replacement_mat = final_mats[index]
                for ref in mat.refs:
                    ref.material = replacement_mat

        self.materials = final_mats
        self.recreate_name_and_id_maps(self.materials)

    def index(self, item: Material):
        return self.materials.index(item)

    def count(self, item: Material):
        return int(item in self)

    def get_by_name(self, name: str) -> Material:
        if name not in self._name_map.keys():
            raise ValueError(f'The material name "{name}" is not found')
        else:
            return self._name_map[name]

    def get_by_id(self, mat_id: int) -> Material:
        if mat_id not in self._id_map.keys():
            raise ValueError(f'The material id "{mat_id}" is not found')
        else:
            return self._id_map[mat_id]

    def renumber_id(self, start_id=1):
        cnt = Counter(start=start_id)
        for mat_id in sorted(self.id_map.keys()):
            mat = self.get_by_id(mat_id)
            mat.id = next(cnt)
        self.recreate_name_and_id_maps(self.materials)

    @property
    def name_map(self) -> Dict[str, Material]:
        return self._name_map

    @property
    def id_map(self) -> Dict[int, Material]:
        return self._id_map

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            for m in self.materials:
                m.units = value
            self._units = value

    def add(self, material) -> Material:
        if material in self:
            existing_mat = self._name_map[material.name]
            for elem in material.refs:
                if elem not in existing_mat.refs:
                    existing_mat.refs.append(elem)
            return existing_mat

        if material.id is None or material.id in self._id_map.keys():
            material.id = len(self.materials) + 1

        self._id_map[material.id] = material
        self._name_map[material.name] = material
        self.materials.append(material)

        return material


class Sections(NumericMapped):
    def __init__(self, sections: Iterable[Section] = None, parent: Part | Assembly = None, units=Units.M):
        sec_id = Counter(1)
        super(Sections, self).__init__(parent=parent)
        sections = [] if sections is None else sections
        self._units = units
        self._sections = sorted(sections, key=attrgetter("name"))

        def section_id_maker(section: Section) -> Section:
            if section.id is None:
                section.id = next(sec_id)
            return section

        [section_id_maker(sec) for sec in self._sections]

        self.recreate_name_and_id_maps(self._sections)

        if len(self._name_map.keys()) != len(self._id_map.keys()):
            import collections

            names = [sec.name for sec in self._sections]
            counts = collections.Counter(names)
            filtered_elements = {element: count for element, count in counts.items() if count > 1}
            logger.warning(f"The following sections are non-unique '{filtered_elements}'")

    def renumber_id(self, start_id=1):
        cnt = Counter(start=start_id)
        for mat_id in sorted(self.id_map.keys()):
            mat = self.get_by_id(mat_id)
            mat.id = next(cnt)
        self.recreate_name_and_id_maps(self._sections)

    def __contains__(self, item):
        return item.name in self._name_map.keys()

    def __len__(self):
        return len(self._sections)

    def __iter__(self) -> Iterable[Section]:
        return iter(self._sections)

    def __getitem__(self, index):
        result = self._sections[index]
        return Sections(result) if isinstance(index, slice) else result

    def __add__(self, other: Sections):
        if self.parent is None:
            logger.error(f'Parent is None for Sections container "{self}"')
        for sec in other:
            sec.parent = self.parent
        other.renumber_id(self.max_id + 1)
        return Sections(chain(self, other), parent=self.parent)

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Sections({rpr.repr(self._sections) if self._sections else ''})"

    def merge_sections_by_properties(self):
        models = []
        final_sections = []
        for i, sec in enumerate(self.sections):
            if sec not in models:
                models.append(sec)
                final_sections.append(sec)
            else:
                index = models.index(sec)
                replacement_sec = models[index].parent
                for ref in sec.refs:
                    ref.section = replacement_sec

        self._sections = final_sections
        self.recreate_name_and_id_maps(self._sections)

    def index(self, item):
        index = bisect_left(self._sections, item)
        if (index != len(self._sections)) and (self._sections[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def count(self, item: Section) -> int:
        return int(item in self)

    def get_by_name(self, name: str) -> Section:
        if name not in self._name_map.keys():
            raise ValueError(f'The section id "{name}" is not found')

        return self._name_map[name]

    def get_by_id(self, sec_id: int) -> Section:
        if sec_id not in self._id_map.keys():
            raise ValueError(f'The node id "{sec_id}" is not found')

        return self._id_map[sec_id]

    @property
    def id_map(self) -> dict[int, Section]:
        return self._id_map

    @property
    def name_map(self) -> dict[str, Section]:
        return self._name_map

    def add(self, section: Section) -> Section:
        if section.name is None:
            raise Exception("Name is not allowed to be None.")

        # Note: Evaluate if parent should be "Sections" not Part object?
        if section.parent is None:
            section.parent = self._parent

        if section in self._sections:
            index = self._sections.index(section)
            existing_section = self._sections[index]
            for elem in section.refs:
                elem.section = existing_section
                if elem not in existing_section.refs:
                    existing_section.refs.append(elem)
            return existing_section

        if section.name in self._name_map.keys():
            logger.info(f'Section with same name "{section.name}" already exists. Will use that section instead')
            existing_section = self._name_map[section.name]
            for elem in section.refs:
                if section == elem.section:
                    elem.section = existing_section
                else:
                    elem.taper = existing_section
                if elem not in existing_section.refs:
                    existing_section.refs.append(elem)
            return existing_section

        if section.id is None:
            section.id = self.max_id + 1

        if len(self._sections) > 0 and section.id in self._id_map.keys():
            section.id = self.max_id + 1

        self._sections.append(section)
        self._id_map[section.id] = section
        self._name_map[section.name] = section

        return section

    @property
    def sections(self) -> list[Section]:
        return self._sections

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            for m in self._sections:
                m.units = value
            self._units = value


class Nodes:
    def __init__(self, nodes=None, parent=None, from_np_array=None):
        self._parent = parent

        if from_np_array is not None:
            self._array = from_np_array
            nodes = self._np_array_to_nlist(from_np_array)
        else:
            nodes = [] if nodes is None else nodes

        self._nodes = list(nodes)

        if len(tuple(set(self._nodes))) != len(self._nodes):
            raise DuplicateNodes("Duplicate Nodes not allowed in a Nodes object")

        self._idmap = dict()
        self._bbox = None
        self._maxid = 0
        if len(self._nodes) > 0:
            self._sort()
            self._maxid = max(self._idmap.keys())
            self._bbox = self._get_bbox()

    def _sort(self):
        self._nodes = sorted(self._nodes, key=attrgetter("x", "y", "z"))
        try:
            self._idmap = {n.id: n for n in sorted(self._nodes, key=attrgetter("id"))}
        except TypeError as e:
            raise TypeError(e)

    def renumber(self, start_id: int = 1, renumber_map: dict = None):
        """Ensures that the node numberings starts at 1 and has no holes in its numbering."""
        if renumber_map is not None:
            self._renumber_from_map(renumber_map)
        else:
            self._renumber_linearly(start_id)

        self._sort()
        self._maxid = max(self._idmap.keys()) if len(self._nodes) > 0 else 0
        self._bbox = self._get_bbox() if len(self._nodes) > 0 else None

    def _renumber_linearly(self, start_id):
        for i, n in enumerate(sorted(self._nodes, key=attrgetter("id")), start=start_id):
            if i != n.id:
                n.id = i

    def _renumber_from_map(self, renumber_map):
        for n in sorted(self._nodes, key=attrgetter("id")):
            n.id = renumber_map[n.id]

    def _np_array_to_nlist(self, np_array):
        from ada import Node

        return [Node(row[1:], int(row[0]), parent=self._parent) for row in np_array]

    def to_np_array(self, include_id=False):
        if include_id:
            return np.array([(n.id, *n.p) for n in self._nodes])
        else:
            return np.array([n.p for n in self._nodes])

    def to_fem_nodes(self) -> FemNodes:
        from ada.fem.results.common import FemNodes

        node_refs = self.to_np_array(include_id=True)
        identifiers = node_refs[:, 0]
        coords = node_refs[:, 1:]

        return FemNodes(coords, identifiers)

    def __contains__(self, item):
        return item in self._nodes

    def __len__(self):
        return len(self._nodes)

    def __iter__(self) -> Iterable[Node]:
        return iter(self._nodes)

    def __getitem__(self, index):
        result = self._nodes[index]
        return Nodes(result) if isinstance(index, slice) else result

    def __eq__(self, other):
        if not isinstance(other, Nodes):
            return NotImplemented
        return self._nodes == other._nodes

    def __ne__(self, other):
        if not isinstance(other, Nodes):
            return NotImplemented
        return self._nodes != other._nodes

    def __add__(self, other: Nodes):
        for n in other.nodes:
            n.parent = self.parent
        return Nodes(chain(self._nodes, other.nodes))

    def __repr__(self):
        return f"Nodes({len(self._nodes)}, min_id: {self.min_nid}, max_id: {self.max_nid})"

    def index(self, item):
        index = bisect_left(self._nodes, item)
        if (index != len(self._nodes)) and (self._nodes[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def count(self, item):
        return int(item in self)

    def move(self, move: Iterable[float, float, float] = None, rotate: Rotation = None):
        """A method for translating and/or rotating your model."""

        def moving(no):
            no.p = no.p + move

        def map_rotations(no, p):
            no.p = p

        if rotate is not None:
            origin = np.array(rotate.origin)
            rot_mat = rotate.to_rot_matrix()
            vectors = np.array([n.p - origin for n in self._nodes])
            res = np.matmul(vectors, rot_mat.T)
            [map_rotations(n, p + origin) for n, p in zip(self._nodes, res)]

        if move is not None:
            move = np.array(move)
            list(map(moving, self._nodes))

        self._sort()

    def from_id(self, nid: int):
        if nid not in self._idmap.keys():
            raise ValueError(f'The node id "{nid}" is not found')
        else:
            return self._idmap[nid]

    def _get_bbox(self):
        if len(self._nodes) == 0:
            raise ValueError("No Nodes are found")
        nodes_yids = sorted(self._nodes, key=attrgetter("y"))
        nodes_zids = sorted(self._nodes, key=attrgetter("z"))
        xmin, xmax = self._nodes[0][0], self._nodes[-1][0]
        ymin, ymax = nodes_yids[0][1], nodes_yids[-1][1]
        zmin, zmax = nodes_zids[0][2], nodes_zids[-1][2]
        return (xmin, xmax), (ymin, ymax), (zmin, zmax)

    @property
    def dmap(self) -> Dict[str, Node]:
        return self._idmap

    def bbox(self):
        if self._bbox is None:
            self._bbox = self._get_bbox()
        return self._bbox

    def vol_cog(self):
        bbox = self.bbox()
        return tuple([(bbox[i][0] + bbox[i][1]) / 2 for i in range(3)])

    @property
    def max_nid(self) -> int:
        return max(self.dmap.keys()) if len(self.dmap.keys()) > 0 else 0

    @property
    def min_nid(self) -> int:
        return min(self.dmap.keys()) if len(self.dmap.keys()) > 0 else 0

    @property
    def nodes(self) -> list[Node]:
        return self._nodes

    def get_by_volume(
        self, p=None, vol_box=None, vol_cyl=None, tol=Config().general_point_tol, single_member=False
    ) -> list[Node] | Node:
        """

        :param p: Point
        :param vol_box: Additional point to find nodes inside a rectangular box
        :param vol_cyl: (radius, height, cylinder thickness). Note! Radius is measured to outside of cylinder wall
        :param tol: Point tolerance
        :return:
        """
        p = np.array(p) if type(p) is (list, tuple) else p
        if p is not None and vol_cyl is None and vol_box is None:
            vol = [(coord - tol, coord + tol) for coord in p]
        elif vol_box is not None:
            vol = list(zip(p, vol_box))
        elif vol_cyl is not None and p is not None:
            r, h, t = vol_cyl
            vol = [
                (p[0] - r - tol, p[0] + r + tol),
                (p[1] - r - tol, p[1] + r + tol),
                (p[2] - tol, p[2] + tol + h),
            ]
        else:
            raise Exception("No valid search input provided. None is returned")

        vol_min, vol_max = zip(*vol)
        xmin = bisect_left(self._nodes, Node(vol_min))
        xmax = bisect_right(self._nodes, Node(vol_max))

        xlist = sorted(self._nodes[xmin:xmax], key=attrgetter("y"))
        ysorted = [n.y for n in xlist]
        ymin = bisect_left(ysorted, vol_min[1])
        ymax = bisect_right(ysorted, vol_max[1])

        ylist = sorted(xlist[ymin:ymax], key=attrgetter("z"))
        zsorted = [n.z for n in ylist]
        zmin = bisect_left(zsorted, vol_min[2])
        zmax = bisect_right(zsorted, vol_max[2])

        simplesearch = ylist[zmin:zmax]

        if vol_cyl is not None:
            r, h, t = vol_cyl
            pt1_ = p + np.array([0, 0, -h])
            pt2_ = p + np.array([0, 0, +h])

            def eval_p_in_cyl(no):
                if t == r:
                    if points_in_cylinder(pt1_, pt2_, r, no.p) is True:
                        return no
                else:
                    eval1 = points_in_cylinder(pt1_, pt2_, r + t, no.p)
                    eval2 = points_in_cylinder(pt1_, pt2_, r - t, no.p)
                    if eval1 is True and eval2 is False:
                        return no
                return None

            result = list(filter(None, [eval_p_in_cyl(q) for q in simplesearch]))
        else:
            result = list(simplesearch)

        if len(result) == 0:
            logger.info(f"No vertices found using {p=}, {vol_box=}, {vol_cyl=} and {tol=}")
            return result

        if single_member:
            if len(result) != 1:
                logger.warning(f"Returning member at index=0 despite {len(result)=}. Please check your results")
            return result[0]

        return result

    def add(self, node: Node, point_tol: float = Config().general_point_tol, allow_coincident: bool = False) -> Node:
        """Insert node into sorted list"""

        def insert_node(n, i):
            new_id = int(self._maxid + 1) if len(self._nodes) > 0 else 1
            if n.id in self._idmap.keys() or n.id is None:
                n.id = new_id

            self._nodes.insert(i, n)
            self._idmap[n.id] = n
            self._bbox = None
            self._maxid = n.id if n.id > self._maxid else self._maxid

        index = bisect_left(self._nodes, node)
        if (len(self._nodes) != 0) and allow_coincident is False:
            res = self.get_by_volume(node.p, tol=point_tol)
            if len(res) == 1:
                nearest_node = res[0]
                vlen = vector_length(nearest_node.p - node.p)
                if vlen < point_tol:
                    logger.debug(f'Replaced new node with node id "{nearest_node.id}" found within point tolerances')
                    return nearest_node

        insert_node(node, index)

        if node.parent is None:
            node.parent = self.parent

        return node

    def remove(self, nodes: Union[Node, Iterable[Node]]):
        """Remove node(s) from the nodes container"""
        nodes = list(nodes) if isinstance(nodes, Iterable) else [nodes]
        ids = [node.id for node in nodes]
        for node_id in ids:
            if node_id in self._idmap.keys():
                self._idmap.pop(node_id)
            else:
                logger.error(f"'{node_id}' not found in node-container.")
        self._nodes = list(self._idmap.values())
        self.renumber()

    def remove_standalones(self) -> None:
        """Remove nodes that are without any usage references"""
        self.remove(filter(lambda x: not x.has_refs, self._nodes))

    def merge_coincident(self, tol: float = Config().general_point_tol) -> None:
        """
        Merge nodes which are within the standard default of Nodes.get_by_volume. Nodes merged into the node connected
        to most elements.
        :return:
        """

        def replace_duplicate_nodes(duplicates: Iterable[Node], new_node: Node):
            if duplicates and len(new_node.refs) >= np.max(list(map(lambda x: len(x.refs), duplicates))):
                for duplicate_node in duplicates:
                    replace_node(duplicate_node, new_node)
                    self.remove(duplicate_node)

        for node in filter(lambda x: x.has_refs, self._nodes):
            duplicate_nodes = list(
                sorted(
                    filter(lambda x: x.id != node.id, self.get_by_volume(node.p, tol=tol)), key=lambda x: len(x.refs)
                )
            )
            replace_duplicate_nodes(duplicate_nodes, node)

        self._sort()

    def rounding_node_points(self, precision: int = Config().general_precision) -> None:
        """Rounds all nodes to set precision"""
        for node in self.nodes:
            node.p_roundoff(precision=precision)

    @property
    def parent(self) -> Union[Part, FEM]:
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value
