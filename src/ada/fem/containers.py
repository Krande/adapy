from __future__ import annotations

import logging
from bisect import bisect_left
from dataclasses import dataclass
from functools import partial
from itertools import chain, groupby
from operator import attrgetter
from typing import Iterable, List, Union

import numpy as np

from ada.concepts.containers import Materials
from ada.concepts.points import Node
from ada.core.utils import Counter

from .elements import Elem, FemSection, MassTypes
from .sets import FemSet, SetTypes
from .shapes import ElemShapes, ElemType


@dataclass
class COG:
    p: np.array
    tot_mass: float
    tot_vol: float
    sh_mass: float
    bm_mass: float
    no_mass: float


class FemElements:
    """

    :param elements:
    :param fem_obj:
    :type fem_obj: ada.FEM
    """

    def __init__(self, elements=None, fem_obj=None, from_np_array=None):
        """:type fem_obj:"""
        self._fem_obj = fem_obj
        if from_np_array is not None:
            elements = self.elements_from_array(from_np_array)

        self._elements = list(sorted(elements, key=attrgetter("id"))) if elements is not None else []
        self._idmap = {e.id: e for e in self._elements} if len(self._elements) > 0 else dict()

        if len(self._idmap) != len(self._elements):
            raise ValueError("Unequal length of idmap and elements. Might indicate doubly defined element id's")

        self._by_types = None
        self._group_by_types()

    def renumber(self, start_id=1):

        elid = Counter(start_id)

        def mapid2(el: Elem):
            el.id = next(elid)

        list(map(mapid2, self._elements))
        self._idmap = {e.id: e for e in self._elements} if len(self._elements) > 0 else dict()

        self._group_by_types()

    def link_nodes(self):
        """Link element nodes with the parent fem node collection"""

        def grab_nodes(elem: Elem):
            nodes = [self._fem_obj.nodes.from_id(no) for no in elem.nodes if type(no) in (int, np.int32)]
            if len(nodes) != len(elem.nodes):
                raise ValueError("Unable to convert element nodes")
            elem._nodes = nodes

        list(map(grab_nodes, self._elements))

    def _build_sets_from_elsets(self, elset, elem_iter):
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

        def calc_sh_elem(el: Elem):
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

        def calc_bm_elem(el: Elem):
            el.fem_sec.section.properties.calculate()
            nodes_ = el.fem_sec.get_offset_coords()
            elem_len = vector_length(nodes_[-1] - nodes_[0])
            vol_ = el.fem_sec.section.properties.Ax * elem_len
            mass = vol_ * el.fem_sec.material.model.rho
            center = sum([e.p for e in el.nodes]) / len(el.nodes)

            return mass, center, vol_

        def calc_mass_elem(el: Elem):
            if el.mass_props.type != MassTypes.MASS:
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
    def parent(self):
        return self._fem_obj

    @parent.setter
    def parent(self, value):
        self._fem_obj = value

    @property
    def max_el_id(self):
        return max(self._idmap.keys())

    @property
    def min_el_id(self):
        return min(self._idmap.keys())

    @property
    def elements(self) -> List[Elem]:
        return self._elements

    @property
    def solids(self):
        return filter(lambda x: x.type in ElemShapes.volume, self.stru_elements)

    @property
    def shell(self):
        return filter(lambda x: x.type in ElemShapes.shell, self.stru_elements)

    @property
    def lines(self):
        return filter(lambda x: x.type in ElemShapes.lines, self.stru_elements)

    @property
    def connectors(self):
        return filter(lambda x: x.type in ElemShapes.connectors, self.stru_elements)

    @property
    def masses(self) -> Iterable[Elem]:
        return filter(lambda x: x.type in MassTypes.all, self._elements)

    @property
    def stru_elements(self) -> Iterable[Elem]:
        return filter(lambda x: x.type not in ["MASS", "SPRING1"], self._elements)

    def from_id(self, el_id: int) -> Elem:
        el = self._idmap.get(el_id, None)
        if el is None:
            raise ValueError(f'The elem id "{el_id}" is not found')
        return el

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
        return self._idmap

    def add(self, elem: Elem):
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

    def remove(self, elems: Union[Elem, List[Elem]]):
        """Remove elem or list of elements from container"""
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
            self._by_types = groupby(self._elements, key=attrgetter("type", SetTypes.ELSET))
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
    def __init__(self, sections: Iterable[FemSection] = None, fem_obj=None):
        """:type fem_obj: ada.FEM"""
        self._fem_obj = fem_obj
        self._sections = list(sections) if sections is not None else []
        by_types = self._groupby()
        self._lines = by_types["lines"]
        self._shells = by_types["shells"]
        self._solids = by_types["solids"]
        self._dmap = {e.name: e for e in self._sections} if len(self._sections) > 0 else dict()

        if len(self._sections) > 0 and fem_obj is not None:
            self._link_data()

    def _map_materials(self, fem_sec: FemSection, mat_repo: Materials):
        if type(fem_sec.material) is str:
            fem_sec._material = mat_repo.get_by_name(fem_sec.material)

    def _map_elsets(self, fem_sec: FemSection, elset_repo):
        if type(fem_sec.elset) is str:
            if fem_sec.elset not in elset_repo:
                raise ValueError(f'The element set "{fem_sec.elset}" is not imported. ')
            fem_sec._elset = elset_repo[fem_sec.elset]
        elif type(fem_sec) is FemSection:
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
        return FemSections(chain(self.sections, other.sections))

    def __repr__(self):
        return f"FemSections(Beams: {len(self.lines)}, Shells: {len(self.shells)}, Solids: {len(self.solids)})"

    @property
    def parent(self):
        return self._fem_obj

    @parent.setter
    def parent(self, value):
        self._fem_obj = value

    @property
    def sections(self) -> Iterable[FemSection]:
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

    @property
    def dmap(self):
        return self._dmap

    def index(self, item):
        index = bisect_left(self._sections, item)
        if (index != len(self._sections)) and (self._sections[index] == item):
            return index
        raise ValueError(f"{repr(item)} not found")

    def _map_femsec_to_elem(self, elem: Elem, fem_sec: FemSection):
        elem.fem_sec = fem_sec

    def add(self, sec: FemSection):
        if sec.name in self.dmap.keys() or sec.name is None:
            raise ValueError(f'Section name "{sec.name}" already exists')

        self._sections.append(sec)
        if sec.type == ElemType.LINE:
            self._lines.append(sec)
        elif sec.type == ElemType.SHELL:
            self._shells.append(sec)
        else:
            self._solids.append(sec)

        list(map(partial(self._map_femsec_to_elem, fem_sec=sec), sec.elset.members))
        self._dmap[sec.name] = sec


class FemSets:
    def __init__(self, sets: List[FemSet] = None, fem_obj=None):
        """:type fem_obj: ada.FEM"""
        self._fem_obj = fem_obj
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
    def parent(self):
        """:rtype: ada.FEM"""
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
            if fset.type == SetTypes.ELSET:
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

        if fem_set.type == SetTypes.NSET:
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

    def __add__(self, other: FemSets):
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

    def get_elset_from_name(self, name) -> FemSet:
        if name not in self._elmap.keys():
            raise ValueError(f'The elem id "{name}" is not found')
        else:
            return self._elmap[name]

    def get_nset_from_name(self, name) -> FemSet:
        if name not in self._nomap.keys():
            raise ValueError(f'The node id "{name}" is not found')
        else:
            return self._nomap[name]

    @property
    def elements(self):
        return self._elmap

    @property
    def nodes(self):
        return self._nomap

    @property
    def sets(self):
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

    def add(self, fe_set: FemSet, append_suffix_on_exist=False) -> FemSet:
        if fe_set.type == SetTypes.NSET:
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
