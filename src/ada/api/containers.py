from __future__ import annotations

import reprlib
from bisect import bisect_left, bisect_right, insort
from itertools import chain
from operator import attrgetter
from typing import TYPE_CHECKING, Dict, Iterable, List, Union

import numpy as np

from ada.api.beams import Beam, BeamTapered
from ada.api.exceptions import DuplicateNodes
from ada.api.nodes import Node, replace_node
from ada.api.plates.base_pl import Plate
from ada.api.transforms import Rotation
from ada.base.units import Units
from ada.config import Config, logger
from ada.core.utils import Counter, roundoff
from ada.core.vector_utils import points_in_cylinder, vector_length
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
from collections.abc import MutableSequence
from typing import Any, Callable, Generic, Optional, TypeVar

T = TypeVar("T")
K = TypeVar("K")  # for generic ID
N = TypeVar("N", bound=int)  # numeric ID


class IndexedCollection(MutableSequence[T], Generic[T, K, N]):
    def __init__(
        self,
        items: Iterable[T] = (),
        *,
        sort_key: Callable[[T], Any],
        id_key: Callable[[T], K],
        name_key: Optional[Callable[[T], str]] = None,
        numeric_id_key: Optional[Callable[[T], N]] = None,
    ):
        self._sort_key = sort_key
        self._id_key = id_key
        self._name_key = name_key
        self._numeric_id_key = numeric_id_key

        self._items = sorted(items, key=sort_key)
        # always build the primary id map
        self._idmap = {id_key(i): i for i in self._items}
        # build a name‐map if requested
        if name_key:
            self._nmap = {name_key(i): i for i in self._items}
        # build a numeric‐id map if requested
        if numeric_id_key:
            self._num_map = {numeric_id_key(i): i for i in self._items}

    # — all your MutableSequence methods here —
    # __len__, __getitem__, __delitem__, __setitem__, insert
    # --- MutableSequence methods ---
    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, i):
        return self._items[i]

    def __delitem__(self, i):
        item = self._items.pop(i)
        self._idmap.pop(self._id_key(item), None)
        self._nmap.pop(self._name_key(item), None)

    def __setitem__(self, i, item: T):
        # replace at index i
        old = self._items[i]
        del self[i]
        self.insert(i, item)

    def insert(self, i: int, item: T) -> None:
        # enforce uniqueness by name or id if you like
        _id = self._id_key(item)
        _name = self._name_key(item)
        if _id in self._idmap or _name in self._nmap:
            raise ValueError(f"Duplicate {_name=} or {_id=}")
        insort(self._items, item, key=self._sort_key)
        self._idmap[_id] = item
        self._nmap[_name] = item

    def add(self, item: T) -> None:
        self.insert(0, item)

    def __contains__(self, item: T) -> bool:
        return self._id_key(item) in self._idmap

    def from_id(self, val: K) -> Optional[T]:
        return self._idmap.get(val)

    def from_name(self, name: str) -> Optional[T]:
        return getattr(self, "_nmap", {}).get(name)

    def from_numeric_id(self, num: int) -> Optional[T]:
        return getattr(self, "_num_map", {}).get(num)


class BaseCollections:
    """The Base class for all collections"""

    def __init__(self, parent: Part):
        self._parent = parent

    @property
    def parent(self) -> Part:
        return self._parent


class Beams(IndexedCollection[Beam, str, int]):
    def __init__(self, beams: Iterable[Beam] = (), parent=None):
        super().__init__(
            items=beams,
            sort_key=lambda b: b.name,
            id_key=lambda b: b.guid,
            name_key=lambda b: b.name,
        )
        self._parent = parent

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

        bm_list1 = [(bm.name, bm.n1.x, bm.n1.y, bm.n1.z) for bm in sorted(self._items, key=lambda bm: bm.n1.x)]
        bm_list2 = [(bm.name, bm.n2.x, bm.n2.y, bm.n2.z) for bm in sorted(self._items, key=lambda bm: bm.n2.x)]

        return set([self.from_name(bm_id) for bms_ in (bm_list1, bm_list2) for bm_id in sort_beams(bms_)])

    def add(self, beam: Beam) -> Beam:
        if beam.name is None:
            raise ValueError("Name may not be None")
        if beam.name in self._nmap:
            return self._nmap[beam.name]

        # any Beam-specific wiring…
        super().add(beam)
        beam.add_beam_to_node_refs()
        return beam


class Plates(IndexedCollection[Plate, str, int]):
    def __init__(self, plates: Iterable[Plate] = (), parent: Part = None):
        super().__init__(
            items=plates,
            sort_key=lambda p: p.name,
            id_key=lambda p: p.guid,
            name_key=lambda p: p.name,
        )
        self._parent = parent

    def add(self, plate: Plate) -> Plate:
        if plate.name is None:
            raise ValueError("Name may not be None")
        existing = self._nmap.get(plate.name)
        if existing:
            return existing
        # handle material as before…
        mat = self._parent.materials.add(plate.material)
        if mat is not None:
            plate.material = mat

        super().add(plate)
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

        logger.info(f"Connection search finished. Found a total of {len(self._connections)} connections")


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

    def add(self, material: Material) -> Material:
        name_map = self._name_map
        id_map = self._id_map
        mats = self.materials

        # 1) Fast-path existing: use dict.get instead of “in self” or keys()
        existing = name_map.get(material.name)
        if existing is not None:
            # merge refs in one pass, avoiding O(n²) list lookups
            existing_refs = existing.refs
            # build a set for O(1) membership tests
            seen = set(existing_refs)
            # only append the new ones
            for ref in material.refs:
                if ref not in seen:
                    existing_refs.append(ref)
                    seen.add(ref)
            return existing

        # 2) Assign a fresh id if needed
        mat_id = material.id
        if mat_id is None or mat_id in id_map:
            mat_id = len(mats) + 1
            material.id = mat_id

        # 3) Insert in O(1)
        mats.append(material)
        id_map[mat_id] = material
        name_map[material.name] = material

        return material


class Sections(NumericMapped):
    def __init__(self, sections: Iterable[Section] = None, parent: Part | Assembly = None, units=Units.M):
        super().__init__(parent=parent)
        self._units = units
        self._sections: list[Section] = sorted(sections or [], key=attrgetter("name"))
        self._id_map: dict[int, Section] = {}
        self._name_map: dict[str, Section] = {}
        # assign IDs and build maps
        sec_id = Counter(start=1)
        for sec in self._sections:
            if sec.id is None:
                sec.id = next(sec_id)
            self._id_map[sec.id] = sec
            self._name_map[sec.name] = sec

        if len(self._name_map) != len(self._id_map):
            names = [sec.name for sec in self._sections]
            duplicates = {n: c for n, c in Counter(names).items() if c > 1}
            logger.warning(f"The following sections are non-unique: {duplicates!r}")

    @property
    def max_id(self) -> int:
        return max(self._id_map.keys(), default=0)

    def renumber_id(self, start_id: int = 1) -> None:
        cnt = Counter(start=start_id)
        for old_id in sorted(self._id_map):
            sec = self._id_map[old_id]
            sec.id = next(cnt)
        # rebuild maps
        self._id_map = {sec.id: sec for sec in self._sections}
        self._name_map = {sec.name: sec for sec in self._sections}

    def __len__(self) -> int:
        return len(self._sections)

    def __iter__(self):
        return iter(self._sections)

    def __getitem__(self, idx):
        result = self._sections[idx]
        return Sections(result, parent=self.parent) if isinstance(idx, slice) else result

    def __add__(self, other: Sections) -> Sections:
        if self.parent is None:
            logger.error(f'Parent is None for Sections container "{self}"')
        for sec in other:
            sec.parent = self.parent
        other.renumber_id(self.max_id + 1)
        return Sections(chain(self, other), parent=self.parent)

    def __repr__(self) -> str:
        r = reprlib.Repr()
        r.maxlist = 8
        r.maxlevel = 1
        return f"{self.__class__.__name__}({r.repr(self._sections)})"

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
    def sections(self) -> list[Section]:
        return self._sections

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, new_units):
        if isinstance(new_units, str):
            new_units = Units.from_str(new_units)
        if new_units != self._units:
            for sec in self._sections:
                sec.units = new_units
            self._units = new_units

    @property
    def id_map(self) -> dict[int, Section]:
        return self._id_map

    @property
    def name_map(self) -> dict[str, Section]:
        return self._name_map

    def add(self, section: Section) -> Section:
        if section.name is None:
            raise ValueError("Section.name may not be None")

        # ensure correct parent
        section.parent = section.parent or self.parent

        # quick lookup by name
        if section.name in self._name_map:
            existing = self._name_map[section.name]
            # dedupe refs using a set
            existing_refs = set(existing.refs)
            for ref in section.refs:
                # redirect the ref to the existing section
                if isinstance(ref, BeamTapered):
                    if existing.equal_props(ref.section):
                        ref.section = existing
                    elif existing.equal_props(ref.taper):
                        ref.taper = existing
                else:
                    if existing.equal_props(ref.section):
                        ref.section = existing
                # append only new refs
                if ref not in existing_refs:
                    existing.refs.append(ref)
                    existing_refs.add(ref)
            return existing

        # assign a fresh unique id
        if section.id is None or section.id in self._id_map:
            section.id = self.max_id + 1

        # insert into sorted list by name
        insort(self._sections, section, key=attrgetter("name"))

        # update maps
        self._id_map[section.id] = section
        self._name_map[section.name] = section

        return section


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
