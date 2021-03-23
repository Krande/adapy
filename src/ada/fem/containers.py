from bisect import bisect_left
from itertools import chain, groupby
from operator import attrgetter

import numpy as np


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

        self._elements = list(elements) if elements is not None else []
        self._idmap = {e.id: e for e in self._elements} if len(self._elements) > 0 else dict()

        if len(self._elements) > 0:
            self._by_types = groupby(self._elements, key=attrgetter("type", "elset"))
        else:
            self._by_types = dict()

    def renumber(self):
        from ada.core.utils import Counter

        elid = Counter(0)

        def mapid2(el):
            """

            :param el:
            :type el: ada.fem.Elem
            :return:
            """
            el.id = next(elid)

        list(map(mapid2, self._elements))
        self._idmap = {e.id: e for e in self._elements} if len(self._elements) > 0 else dict()

        if len(self._elements) > 0:
            self._by_types = groupby(self._elements, key=attrgetter("type", "elset"))
        else:
            self._by_types = dict()

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
        """

        :param elset:
        :param elem_iter:
        """
        from ada.fem import FemSet

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
            self._idmap.pop(el.id)
        self._elements = list(self._idmap.values())
        self._by_types = groupby(self._elements, key=attrgetter("type", "elset"))
        p = elset.parent
        p.sets.elements.pop(elset.name)

    def __contains__(self, item):
        return item.id in self._idmap.keys()

    def __len__(self):
        return len(self._elements)

    def __iter__(self):
        return iter(self._elements)

    def __getitem__(self, index):
        result = self._elements[index]
        return FemElements(result) if isinstance(index, slice) else result

    def __add__(self, other):
        return FemElements(chain(self.elements, other.elements), self._fem_obj)

    def __repr__(self):
        data = {}
        for key, val in groupby(self._elements, key=attrgetter("type")):
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
            vol = t * area
            mass = vol * el.fem_sec.material.model.rho
            mass_per_node = mass / len(el.nodes)

            # Have not added offset to fem_section yet
            # adjusted_nodes = [e.p+t*normal for e in el.nodes]

            return mass_per_node, [e for e in el.nodes], vol

        def calc_bm_elem(el):
            """

            :param el:
            :type el: ada.Elem
            """
            el.fem_sec.section.properties.calculate()
            nodes = el.fem_sec.get_offset_coords()
            elem_len = vector_length(nodes[-1] - nodes[0])
            vol = el.fem_sec.section.properties.Ax * elem_len
            mass = vol * el.fem_sec.material.model.rho
            mass_per_node = mass / 2

            return mass_per_node, [el.nodes[0], el.nodes[-1]], vol

        def calc_mass_elem(el):
            """

            :param el:
            :type el: ada.Elem
            """
            if el.mass_props.type != "MASS":
                raise NotImplementedError(f'Mass type "{el.mass_props.type}" is not yet implemented')
            mass = el.mass_props.mass
            nodes = el.nodes
            vol = 0.0
            return mass, nodes, vol

        sh = list(chain(map(calc_sh_elem, self.shell)))
        bm = list(chain(map(calc_bm_elem, self.beams)))
        ma = list(chain(map(calc_mass_elem, self.masses)))
        mcogx = 0.0
        mcogy = 0.0
        mcogz = 0.0
        tot_mass = 0.0
        tot_vol = 0.0

        sh_mass = sum([r[0] for r in sh])
        bm_mass = sum([r[0] for r in bm])
        no_mass = sum([r[0] for r in ma])

        for m, nodes, vol in sh + bm + ma:
            tot_vol += vol
            for n in nodes:
                tot_mass += m
                mcogx += m * n[0]
                mcogy += m * n[1]
                mcogz += m * n[2]

        cogx = mcogx / tot_mass
        cogy = mcogy / tot_mass
        cogz = mcogz / tot_mass

        return cogx, cogy, cogz, tot_mass, tot_vol, sh_mass, bm_mass, no_mass

    @property
    def elements(self):
        return self._elements

    @property
    def shell(self):
        from ada.fem import Elem

        skipel = ["MASS", "SPRING1"]
        return filter(lambda x: x.type not in skipel and x.type in Elem.shell, self._elements)

    @property
    def beams(self):
        from ada.fem import Elem

        skipel = ["MASS", "SPRING1"]
        return filter(lambda x: x.type not in skipel and x.type in Elem.beam, self._elements)

    @property
    def connectors(self):
        from ada.fem import Elem

        skipel = ["MASS", "SPRING1"]
        return filter(lambda x: x.type not in skipel and x.type in Elem.connectors, self._elements)

    @property
    def masses(self):
        from ada.fem import Mass

        return filter(lambda x: x.type in Mass._valid_types, self._elements)

    @property
    def edges(self):
        """

        :return:
        """

        def grab_nodes(elem):
            return [self._fem_obj.nodes.from_id(elem.nodes[e].id).p for ed_seq in elem.edges_seq for e in ed_seq]

        return list(
            chain.from_iterable(
                map(
                    grab_nodes,
                    filter(lambda x: x.type not in ["MASS", "SPRING1"], self._elements),
                )
            )
        )

    @property
    def edges_alt(self):
        """

        :return:
        """

        def grab_ids(elem):
            return tuple([elem.nodes[e].id for ed_seq in elem.edges_seq for e in ed_seq])

        return map(
            grab_ids,
            filter(lambda x: x.type not in ["MASS", "SPRING1"], self._elements),
        )

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
        self._by_types = dict(groupby(self._elements, key=attrgetter("type")))
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

        self._by_types = groupby(self._elements, key=attrgetter("type", "elset"))


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
        self._beams = by_types["beams"]
        self._shells = by_types["shells"]
        self._solids = by_types["solids"]
        self._dmap = {e.name: e for e in self._sections} if len(self._sections) > 0 else dict()
        if len(self._sections) > 0:
            self._link_data()

    def _map_materials(self, fem_sec, mat_repo):
        """

        :param fem_sec:
        :type fem_sec: ada.fem.FemSection
        """

        if type(fem_sec.material) is str:
            fem_sec._material = mat_repo[fem_sec.material]

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
            beams=list(filter(lambda x: x.type == "beam", self._sections)),
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

    def __add__(self, other):
        return FemSections(chain(self._sections, other._sections))

    def __repr__(self):
        return (
            f"FemSectionsCollection(Beams: {len(self.beams)}, Shells: {len(self.shells)}, Solids: {len(self.solids)})"
        )

    @property
    def beams(self):
        return self._beams

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
        if sec.type == "beam":
            self._beams.append(sec)
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

    @staticmethod
    def is_nset(fs):
        return True if fs.type == "nset" else False

    @staticmethod
    def is_elset(fs):
        return True if fs.type == "elset" else False

    def _map_members(self, fem_set):
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

        if "generate" in fem_set.metadata.keys():
            if fem_set.metadata["generate"] is True and len(fem_set.members) == 0:
                gen_mem = fem_set.metadata["gen_mem"]
                fem_set._members = [i for i in range(gen_mem[0], gen_mem[1] + 1, gen_mem[2])]
                fem_set.metadata["generate"] = False

        if fem_set.type == "nset":
            if len(fem_set.members) == 1 and type(fem_set.members[0]) is str and type(fem_set.members[0]) is not Node:
                fem_set._members = self.nodes[fem_set.members[0]]
            else:
                fem_set._members = list(map(get_nset, fem_set.members))
            fem_set.parent = self._fem_obj
        else:
            fem_set._members = list(map(get_elset, fem_set.members))
            fem_set.parent = self._fem_obj

        return fem_set

    def link_data(self):
        list(map(self._map_members, self._sets))

    def _assemble_sets(self, set_type):
        elsets = dict()
        for elset in filter(set_type, self._sets):
            if elset.name not in elsets.keys():
                elsets[elset.name] = elset
            else:
                self._map_members(elset)
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

        :param fe_set:
        :param append_suffix_on_exist:
        :type fe_set: ada.fem.FemSet
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
        self._map_members(fe_set)
