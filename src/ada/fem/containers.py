from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from functools import partial
from itertools import chain, groupby
from operator import attrgetter
from typing import TYPE_CHECKING, Dict, Iterable, List, Tuple, Union

import numpy as np

from ada.api.containers import Materials
from ada.api.nodes import Node
from ada.config import logger
from ada.core.utils import Counter
from ada.fem.elements import Connector, Elem, Mass, MassTypes
from ada.fem.exceptions.model_definition import FemSetNameExists
from ada.fem.sections import FemSection
from ada.fem.sets import FemSet, SetTypes
from ada.fem.shapes import ElemType
from ada.materials import Material
from ada.sections import Section

if TYPE_CHECKING:
    from ada import FEM, Point
    from ada.fem.results.common import ElementBlock


@dataclass
class COG:
    p: Point
    tot_mass: float = None
    tot_vol: float = None  # what volume is this supposed to represent?
    sh_mass: float = None  # Genie Concept Point Masses and Equipments are defined as Shapes
    bm_mass: float = None
    pl_mass: float = None
    no_mass: float = None


class FemElements:
    """Container class for FEM elements"""

    def __init__(self, elements: Iterable[Elem | Mass | Connector] = None, fem_obj: FEM = None, from_np_array=None):
        self._fem_obj = fem_obj
        if from_np_array is not None:
            elements = self.elements_from_array(from_np_array)

        self._elements = list(sorted(elements, key=attrgetter("id"))) if elements is not None else []
        self._idmap = {e.id: e for e in self._elements} if len(self._elements) > 0 else dict()

        if len(self._idmap) != len(self._elements):
            raise ValueError("Unequal length of idmap and elements. Might indicate doubly defined element id's")

        self._by_types = None
        self._group_by_types()

    def renumber(self, start_id=1, renumber_map: dict = None):
        """Ensures that the node numberings starts at 1 and has no holes in its numbering."""
        if renumber_map is not None:
            self._renumber_from_map(renumber_map)
        else:
            self._renumber_linearly(start_id)

        self._idmap = {e.id: e for e in self._elements} if len(self._elements) > 0 else dict()
        self._group_by_types()

    def _renumber_from_map(self, renumber_map):
        for el in sorted(self._elements, key=attrgetter("id")):
            if isinstance(el, Mass) or el.type == Elem.EL_TYPES.MASS_SHAPES.MASS:
                # Mass elements are points and have been renumbered during node-renumbering
                continue
            el.id = renumber_map[el.id]

    def _renumber_linearly(self, start_id):
        elid = Counter(start_id)
        for el in sorted(self._elements, key=attrgetter("id")):
            el.id = next(elid)

    def link_nodes(self):
        """Link element nodes with the parent fem node collection"""

        def grab_nodes(elem: Elem):
            nodes = [self._fem_obj.nodes.from_id(no) for no in elem.nodes if type(no) in (int, np.int32)]
            if len(nodes) != len(elem.nodes):
                raise ValueError("Unable to convert element nodes")
            elem._nodes = nodes

        list(map(grab_nodes, self._elements))

    def _build_sets_from_elsets(self, elset, elem_iter):
        if elset is not None and isinstance(elset, str):

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
        def to_elem(e):
            nodes = [self._fem_obj.nodes.from_id(n) for n in e[3:] if (n == 0 or np.isnan(n)) is False]
            return Elem(e[0], nodes, e[1], e[2], parent=self._fem_obj)

        return list(map(to_elem, array))

    def build_sets(self):
        """Create sets from attached elset attribute on elements"""
        for elset, elements in groupby(self._elements, key=attrgetter("elset")):
            if elset is None:
                continue
            self._build_sets_from_elsets(elset, elements)

    def remove_elements_by_set(self, elset: FemSet):
        """Remove elements from element set. Will remove element set on completion"""
        for el in elset.members:
            self.remove(el)
        self._sort()
        p = elset.parent
        p.sets.elements.pop(elset.name)

    def remove_elements_by_id(self, ids: Union[int, List[int]]):
        """Remove elements from element ids. Will remove elements on completion"""
        ids = list(ids) if isinstance(ids, Iterable) else [ids]
        for elem_id in ids:
            self.remove(self._idmap[elem_id])
        self._sort()

    def to_elem_blocks(self) -> list[ElementBlock]:
        from ada.fem.results.common import ElementBlock, ElementInfo, FEATypes

        elements = []
        for el_type, el_group in self.group_by_type():
            info = ElementInfo(el_type, FEATypes.GMSH, None)
            elem_data = np.array([tuple([e.id, *[n.id for n in e.nodes]]) for e in el_group], dtype=int)
            el_identifiers = elem_data[:, 0]
            node_refs = elem_data[:, 1:]
            block = ElementBlock(info, node_refs, el_identifiers)
            elements.append(block)
        return elements

    def __contains__(self, item: Elem):
        return item in self._elements

    def __len__(self):
        return len(self._elements)

    def __iter__(self):
        return iter(self._elements)

    def __getitem__(self, index):
        result = self._elements[index]
        return FemElements(result) if isinstance(index, slice) else result

    def __add__(self, other: FemElements):
        other.renumber(self.max_el_id + 1)
        for el in other.elements:
            el.parent = self.parent

        other_num = len(other.elements)
        self_num = len(self.elements)
        final_elem = FemElements(chain.from_iterable([self.elements, other.elements]), self.parent)
        if len(final_elem.elements) != (other_num + self_num):
            raise ValueError("Unequal length of elements after concatenation")

        self._elements = final_elem.elements
        self._idmap = final_elem.idmap
        self._group_by_types()
        return self
        # return final_elem

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

    def calc_cog(self) -> COG:
        """Calculate COG of your FEM model based on element mass distributed to element and nodes.

        Vectorised by (FemSection, node-count) so the rotation matrix
        + material density + thickness are reused across every element
        in a section, and area + centroid are computed with a single
        shoelace+mean over an (M, K, 3) array per (section,
        node-count) bucket. The pre-fix per-element loop reallocated
        the rotation cache hit, built a fresh (K, 3) numpy array,
        ran an individual matmul, and called ``poly_area`` 50 k+
        times — that loop dominated the FEM → GLB hot path
        (``_build_sim_stats`` cost on large meshes was ~160 s of a
        210 s convert before batching).
        """
        from itertools import chain

        from ada.core.vector_transforms import rotation_matrix_csys_rotate
        from ada.core.vector_utils import vector_length

        global_csys = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        rot_cache: dict[int, np.ndarray] = {}

        def _section_rot(fem_sec) -> np.ndarray:
            key = id(fem_sec)
            rm = rot_cache.get(key)
            if rm is None:
                rm = rotation_matrix_csys_rotate(
                    global_csys,
                    [fem_sec.local_x, fem_sec.local_y],
                )
                rot_cache[key] = rm
            return rm

        def _node_coord(n):
            return n.p if isinstance(n, Node) else n

        # ── shells: batched per (section_id, node_count). Different
        # node counts inside one section (TRI3 + QUAD4 mixed) get
        # separate buckets so the stacked array stays rectangular.
        sh_buckets: dict[tuple[int, int], tuple] = {}
        for el in self.shell:
            key = (id(el.fem_sec), len(el.nodes))
            bucket = sh_buckets.get(key)
            if bucket is None:
                sh_buckets[key] = ([el], el.fem_sec)
            else:
                bucket[0].append(el)

        sh_mass = 0.0
        sh_vol = 0.0
        sh_mcog = np.zeros(3, dtype=float)
        for (sec_id, nc), (elems, fem_sec) in sh_buckets.items():
            rot = _section_rot(fem_sec)
            t = float(fem_sec.thickness)
            rho = float(fem_sec.material.model.rho)
            # (M, nc, 3) stack of node coordinates.
            nodes_p = np.array(
                [[_node_coord(n) for n in el.nodes] for el in elems],
                dtype=float,
            )
            # global → local: subtract per-element origin, then matmul
            # by the shared rotation. Shape: (M, nc, 3) @ (3, 3).T
            # broadcasts cleanly because numpy treats the last two
            # axes as the matrix dims.
            origin = nodes_p[:, 0:1, :]  # (M, 1, 3) — broadcasts
            ln = (nodes_p - origin) @ rot.T  # (M, nc, 3)
            x = ln[:, :, 0]
            y = ln[:, :, 1]
            # Shoelace on stacked polygons: identical to poly_area
            # but in a single vectorised pass over M elements.
            x_next = np.roll(x, -1, axis=1)
            y_next = np.roll(y, -1, axis=1)
            area = 0.5 * np.abs(np.sum(x * y_next - x_next * y, axis=1))  # (M,)
            vol = t * area
            mass = vol * rho
            center = nodes_p.mean(axis=1)  # (M, 3)
            sh_mass += float(mass.sum())
            sh_vol += float(vol.sum())
            sh_mcog += (mass[:, None] * center).sum(axis=0)

        # Line + mass elements are usually a small fraction of the
        # total so per-element Python is fine; keep them as-is.
        def calc_bm_elem(el: Elem):
            nodes_ = el.get_offset_coords()
            elem_len = vector_length(nodes_[-1] - nodes_[0])
            vol_ = el.fem_sec.section.properties.Ax * elem_len
            mass = vol_ * el.fem_sec.material.model.rho
            center = sum([e.p for e in el.nodes]) / len(el.nodes)
            return mass, center, vol_

        def calc_mass_elem(el: Mass):
            if el.type != MassTypes.MASS:
                raise NotImplementedError(f'Mass type "{el.mass_props.type}" is not yet implemented')
            return el.mass, el.nodes[0].p, 0.0

        bm = list(chain(map(calc_bm_elem, self.lines)))
        ma = list(chain(map(calc_mass_elem, self.masses)))

        bm_mass = sum(r[0] for r in bm)
        no_mass = sum(r[0] for r in ma)

        tot_mass = sh_mass + bm_mass + no_mass
        tot_vol = sh_vol + sum(r[2] for r in bm) + sum(r[2] for r in ma)
        mcog_ = sh_mcog.copy()
        for m, c, _vol in bm:
            mcog_ += m * np.asarray(c, dtype=float)
        for m, c, _vol in ma:
            mcog_ += m * np.asarray(c, dtype=float)

        cog_ = mcog_ / tot_mass if tot_mass else mcog_

        return COG(cog_, tot_mass, tot_vol, sh_mass, bm_mass, no_mass)

    @property
    def parent(self) -> FEM:
        return self._fem_obj

    @parent.setter
    def parent(self, value: FEM):
        self._fem_obj = value

    @property
    def max_el_id(self):
        if len(self._idmap.keys()) == 0:
            return 0

        return max(self._idmap.keys())

    @property
    def min_el_id(self):
        if len(self._idmap.keys()) == 0:
            return 0

        return min(self._idmap.keys())

    @property
    def elements(self) -> List[Union[Elem, Connector, Mass]]:
        return self._elements

    @property
    def solids(self) -> Iterable[Elem]:
        return filter(lambda x: isinstance(x.type, Elem.EL_TYPES.SOLID_SHAPES), self.stru_elements)

    @property
    def shell(self) -> Iterable[Elem]:
        return filter(lambda x: isinstance(x.type, Elem.EL_TYPES.SHELL_SHAPES), self.stru_elements)

    @property
    def lines(self) -> Iterable[Elem]:
        return filter(lambda x: isinstance(x.type, Elem.EL_TYPES.LINE_SHAPES), self.stru_elements)

    @property
    def lines_hinged(self) -> Iterable[Elem]:
        return filter(lambda x: x.hinge_prop is not None, self.lines)

    @property
    def lines_ecc(self) -> Iterable[Elem]:
        return filter(lambda x: x.eccentricity is not None, self.lines)

    @property
    def connectors(self) -> Iterable[Connector]:
        return filter(lambda x: isinstance(x, Connector), self.elements)

    @property
    def masses(self) -> Iterable[Mass]:
        return filter(lambda x: isinstance(x, Mass), self.elements)

    @property
    def stru_elements(self) -> Iterable[Elem]:
        not_strus = (Mass, Connector)
        return filter(lambda x: isinstance(x, not_strus) is False, self._elements)

    def connector_by_name(self, name: str):
        """Get Connector by name"""
        cmap = {c.name: c for c in self.connectors}
        return cmap.get(name, None)

    def from_id(self, el_id: int) -> Union[Elem, Connector]:
        el = self._idmap.get(el_id, None)
        if el is None:
            spring_id_map = {m.id: m for m in self.parent.springs.values()}
            res = spring_id_map.get(el_id, None)
            if res is not None:
                return res

            raise ValueError(f'The elem id "{el_id}" is not found')
        return el

    def filter_elements(self, keep_elem=None, delete_elem=None):
        """
        Filter which element types you wish to keep using the "keep_elem" list of elem_types or which elements you wish
        to delete using the 'delete_elem' option.

        :param keep_elem:
        :param delete_elem:
        """
        from ada.fem.shapes.definitions import ShapeResolver

        keep_elem = [ShapeResolver.get_el_type_from_str(el_) for el_ in keep_elem] if keep_elem is not None else None
        delete_elem = (
            [ShapeResolver.get_el_type_from_str(el_) for el_ in delete_elem] if delete_elem is not None else None
        )

        def eval_elem(el):
            if keep_elem is not None:
                return True if el.type in keep_elem else False
            else:
                return False if el.type in delete_elem else True

        self._elements = list(filter(eval_elem, self._elements))
        self._by_types = {k: list(v) for k, v in self.group_by_type()}
        self._idmap = {e.id: e for e in self._elements}

    @property
    def idmap(self):
        return self._idmap

    def add(self, elem: Elem, skip_grouping=False) -> Elem:
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

        if skip_grouping:
            return elem

        self._group_by_types()
        return elem

    def remove(self, elems: Union[Elem, List[Elem]]):
        """Remove elem or list of elements from container"""
        elems = list(elems) if isinstance(elems, Iterable) else [elems]
        for elem in elems:
            if elem in self._elements:
                logger.warning(f"Element removal is WIP. Removing element: {elem}")
                self._elements.pop(self._elements.index(elem))
            else:
                logger.error(f"'{elem}' not found in {self.__class__.__name__}-container.")
        # self._sort()

    def group_by_type(self):
        return groupby(sorted(self._elements, key=attrgetter("type")), key=attrgetter("type"))

    def _group_by_types(self):
        if len(self._elements) > 0:
            # Materialize the groupby into {key: [Elem, ...]}. The raw
            # iterator isn't picklable (3.14 drops itertools pickle support
            # entirely) and would also exhaust on first read.
            grouped = groupby(sorted(self._elements, key=attrgetter("type")), key=attrgetter("type", SetTypes.ELSET))
            self._by_types = {k: list(v) for k, v in grouped}
        else:
            self._by_types = dict()

    def _sort(self):
        self._elements = sorted(self._elements, key=attrgetter("id"))
        self._group_by_types()
        self.renumber()

    def merge_with_coincident_nodes(self):
        def remove_duplicate_nodes():
            new_nodes = [n for n in elem.nodes if n.has_refs]
            elem.nodes.clear()
            elem.nodes.extend(new_nodes)

        """
        This does not work according to plan. It seems like it is deleting more and more from the model for each
        iteration
        """
        for elem in filter(lambda x: len(x.nodes) > len([n for n in x.nodes if n.has_refs]), self._elements):
            remove_duplicate_nodes()
            elem.update()

    def update(self):
        self.remove(list(filter(lambda x: x.id is None, self._elements)))


class FemSections:
    def __init__(self, sections: Iterable[FemSection] = None, fem_obj: "FEM" = None):
        self._fem_obj = fem_obj
        self._sections = list(sections) if sections is not None else []
        by_types = self._groupby()
        self._lines = by_types["lines"]
        self._shells = by_types["shells"]
        self._solids = by_types["solids"]
        self._name_map = {e.name: e for e in self._sections} if len(self._sections) > 0 else dict()
        self._id_map = {e.id: e for e in self._sections} if len(self._sections) > 0 else dict()
        if len(self._sections) > 0 and fem_obj is not None:
            self._link_data()

    def _map_by_properties(self) -> Dict[Tuple[Material, Section, tuple, tuple, float], List[FemSection]]:
        merge_map: Dict[Tuple[Material, Section, tuple, tuple, float], List[FemSection]] = dict()
        for fs in self.lines:
            props = (fs.material, fs.section.unique_props(), tuple(), tuple(fs.local_z), tuple(fs.local_y))
            if props not in merge_map.keys():
                merge_map[props] = []

            merge_map[props].append(fs)

        for fs in self.shells:
            props = (fs.material, fs.section, (None,), tuple(), fs.thickness)
            if props not in merge_map.keys():
                merge_map[props] = []

            merge_map[props].append(fs)

        return merge_map

    def merge_by_properties(self):
        parent_part = self.parent.parent
        parent_part.move_all_mats_and_sec_here_from_subparts()

        prop_map = self._map_by_properties()
        remove_fs = []
        for _, fs_list in prop_map.items():
            fs_o = fs_list[0]
            el_o = fs_list[0].elset.members[0]
            for fs in fs_list[1:]:
                for el in fs.elset.members:
                    if el == el_o:
                        continue
                    are_equal = el.fem_sec.has_equal_props(fs_o)
                    if are_equal is True:
                        remove_fs.append(el.fem_sec)
                    el.fem_sec = fs_o
                    fs_o.elset.add_members([el])
        rest_list = list(set(remove_fs))
        self.remove(rest_list)

    def _map_materials(self, fem_sec: FemSection, mat_repo: Materials):
        if isinstance(fem_sec.material, str):
            logger.error(f'Material "{fem_sec.material}" was passed as string')
            fem_sec._material = mat_repo.get_by_name(fem_sec.material.name)

    def _map_elsets(self, fem_sec: FemSection, elset_repo):
        if isinstance(fem_sec.elset, str):
            if fem_sec.elset not in elset_repo:
                raise ValueError(f'The element set "{fem_sec.elset}" is not imported. ')
            fem_sec._elset = elset_repo[fem_sec.elset]
        elif isinstance(fem_sec, FemSection):
            pass
        else:
            raise ValueError("Invalid element set has been attached to this fem section")

    def _link_data(self):
        mat_repo = self._fem_obj.parent.get_assembly().materials
        list(map(partial(self._map_materials, mat_repo=mat_repo), self._sections))

        elsets_repo = self._fem_obj.elsets
        list(map(partial(self._map_elsets, elset_repo=elsets_repo), self._sections))
        [fem_sec.link_elements() for fem_sec in self._sections]

    def _groupby(self):
        return dict(
            lines=list(filter(lambda x: x.type == ElemType.LINE, self._sections)),
            shells=list(filter(lambda x: x.type == ElemType.SHELL, self._sections)),
            solids=list(filter(lambda x: x.type == ElemType.SOLID, self._sections)),
        )

    def __contains__(self, item: FemSection):
        return item in self._sections

    def __len__(self):
        return len(self._sections)

    def __iter__(self) -> Iterable[FemSection]:
        return iter(self._sections)

    def __getitem__(self, index):
        result = self._sections[index]
        return FemSections(result) if isinstance(index, slice) else result

    def __add__(self, other: FemSections):
        return FemSections(chain(self.sections, other.sections), fem_obj=self._fem_obj)

    def __repr__(self):
        return f"FemSections(Beams: {len(self.lines)}, Shells: {len(self.shells)}, Solids: {len(self.solids)})"

    @property
    def parent(self):
        return self._fem_obj

    @parent.setter
    def parent(self, value):
        self._fem_obj = value

    @property
    def sections(self) -> List[FemSection]:
        return self._sections

    @property
    def lines(self) -> List[FemSection]:
        return self._lines

    @property
    def shells(self) -> List[FemSection]:
        return self._shells

    @property
    def solids(self) -> List[FemSection]:
        return self._solids

    @property
    def name_map(self):
        return self._name_map

    @property
    def id_map(self):
        return self._id_map

    def index(self, item):
        index = bisect_left(self._sections, item)
        if (index != len(self._sections)) and (self._sections[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def _map_femsec_to_elem(self, elem: Elem, fem_sec: FemSection):
        elem.fem_sec = fem_sec

    def add(self, sec: FemSection):
        if sec.name in self.name_map.keys() or sec.name is None:
            raise ValueError(f'Section name "{sec.name}" already exists')
        if sec.id in self.id_map.keys() or sec.id is None:
            raise ValueError(f'Section ID "{sec.id}" already exists')

        self._sections.append(sec)
        if sec.type == ElemType.LINE:
            self._lines.append(sec)
        elif sec.type == ElemType.SHELL:
            self._shells.append(sec)
        else:
            self._solids.append(sec)

        [self._map_femsec_to_elem(el, fem_sec=sec) for el in sec.elset.members]
        self._name_map[sec.name] = sec
        self._id_map[sec.id] = sec

    def remove(self, fs_in: Union[List[FemSection], FemSection]):
        if not isinstance(fs_in, list):
            fs_in = [fs_in]

        for fs in fs_in:
            index = self._sections.index(fs)
            rem_fs = self._sections.pop(index)
            if len(rem_fs.elset.refs) == 1 and rem_fs.elset.refs[0] == rem_fs:
                rem_fs.parent.sets.remove(rem_fs.elset)

        self._name_map = {e.name: e for e in self._sections} if len(self._sections) > 0 else dict()
        self._id_map = {e.id: e for e in self._sections} if len(self._sections) > 0 else dict()

        by_types = self._groupby()
        self._lines = by_types["lines"]
        self._shells = by_types["shells"]
        self._solids = by_types["solids"]


class FemSets:
    def __init__(self, sets: list[FemSet] = None, parent: FEM = None):
        self._fem_obj = parent
        self._sets = sorted(sets, key=attrgetter("type", "name")) if sets is not None else []
        # Merge same name sets
        self._nomap = self._assemble_sets(self.is_nset) if len(self._sets) > 0 else dict()
        self._elmap = self._assemble_sets(self.is_elset) if len(self._sets) > 0 else dict()
        self._same_names = dict()
        if len(self._sets) > 0:
            self.link_data()

    def add_references(self) -> None:
        """Add reference to the containing FemSet for each member (node or element)"""

        def _map_ref(el, fem_set):
            el.refs.append(fem_set)

        for _set in self._sets:
            [_map_ref(m, _set) for m in _set.members]

    @property
    def parent(self) -> FEM:
        return self._fem_obj

    @parent.setter
    def parent(self, value):
        self._fem_obj = value

    @staticmethod
    def is_nset(fs):
        return True if fs.type == SetTypes.NSET else False

    @staticmethod
    def is_elset(fs):
        return True if fs.type == SetTypes.ELSET else False

    def _instantiate_all_members(self, fem_set: FemSet):
        from ada.fem import Connector, Mass, Spring

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
            elif type(elref) in (Spring, Mass, Connector):
                return elref
            else:
                raise ValueError(f"Elref type '{type(elref)}' is not recognized")

        def eval_set(fset):
            if fset.type == SetTypes.ELSET:
                el_type = Elem
                get_func = get_elset
            else:
                el_type = Node
                get_func = get_nset

            res = list(filter(lambda x: type(x) is not el_type, fset.members))
            if len(res) > 0:
                fset._members = [get_func(m) for m in fset.members]

        if "generate" in fem_set.metadata.keys():
            if fem_set.metadata["generate"] is True and len(fem_set.members) == 0:
                gen_mem = fem_set.metadata["gen_mem"]
                fem_set._members = [i for i in range(gen_mem[0], gen_mem[1] + 1, gen_mem[2])]
                fem_set.metadata["generate"] = False

        if fem_set.type == SetTypes.NSET:
            if (
                len(fem_set.members) == 1
                and isinstance(fem_set.members[0], str)
                and not isinstance(fem_set.members[0], Node)
            ):
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
        return FemSets(result, parent=self._fem_obj) if isinstance(index, slice) else result

    def __add__(self, other: FemSets):
        # TODO: make default choice for similar named sets in a global settings class
        for name, _set in other.nodes.items():
            _set.parent = self.parent
            if name in self._nomap.keys():
                logger.warning(f'Duplicate Node sets. Node set "{name}" exists')
            self.add(_set, merge_sets_if_duplicate=True)
        for name, _set in other.elements.items():
            _set.parent = self.parent
            if name in self._elmap.keys():
                logger.warning(f'Duplicate element sets. Element set "{name}" exists')
            self.add(_set, merge_sets_if_duplicate=True)
        return self

    def get_elset_from_name(self, name: str) -> FemSet:
        result = self._elmap.get(name, None)
        if result is None:
            raise ValueError(f'The element set "{name}" is not found')

        return result

    def get_nset_from_name(self, name: str) -> FemSet:
        lower_map = {key.lower(): value for key, value in self._nomap.items()}
        result = lower_map.get(name.lower(), None)
        if result is None:
            raise ValueError(f'The nodal set "{name}" is not found')

        return result

    @property
    def elements(self):
        return self._elmap

    @property
    def nodes(self):
        return self._nomap

    @property
    def sets(self) -> list[FemSet]:
        return self._sets

    def index(self, item):
        index = bisect_left(self._sets, item)
        if (index != len(self._sets)) and (self._sets[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def remove(self, fe_set: FemSet):
        i = self._sets.index(fe_set)
        self._sets.pop(i)
        if fe_set.type == SetTypes.NSET:
            self._nomap.pop(fe_set.name)
            if fe_set.name in self._same_names.keys():
                self._same_names.pop(fe_set.name)
        else:
            self._elmap.pop(fe_set.name)
            if fe_set.name in self._same_names.keys():
                self._same_names.pop(fe_set.name)

        # To evalute if dependencies of set should be checked?
        # Against: This is a downstream object. FemSections would point to this set and remove during concatenation.

    def add(self, fe_set: FemSet, append_suffix_on_exist=False, merge_sets_if_duplicate=False) -> FemSet:
        if fe_set.type == SetTypes.NSET:
            if fe_set.name in self._nomap.keys():
                fem_set = self._nomap[fe_set.name]
                new_mem = [m for m in fe_set.members if m.id not in fem_set.members]
                fem_set.add_members(new_mem)
        else:
            if fe_set.name in self._elmap.keys():
                if append_suffix_on_exist is False and merge_sets_if_duplicate is False:
                    raise FemSetNameExists(fe_set.name)

                if merge_sets_if_duplicate is True:
                    o_set = self._elmap[fe_set.name]
                    for mem in fe_set.members:
                        if mem not in o_set.members:
                            o_set.members.append(mem)

                if fe_set.name not in self._same_names.keys():
                    self._same_names[fe_set.name] = 1
                else:
                    self._same_names[fe_set.name] += 1

                fe_set.name = f"{fe_set.name}_{self._same_names[fe_set.name]}"

        self.sets.append(fe_set)
        if fe_set.type == SetTypes.ELSET:
            self._elmap[fe_set.name] = fe_set
        else:
            self._nomap[fe_set.name] = fe_set

        if fe_set.parent is None:
            if self._fem_obj is None:
                raise ValueError("Fem obj cannot be none")
            fe_set.parent = self._fem_obj

        self._instantiate_all_members(fe_set)

        return fe_set
