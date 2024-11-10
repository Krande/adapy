from dataclasses import dataclass, field
from itertools import groupby
from operator import itemgetter
from typing import TYPE_CHECKING, Iterable

from ada.config import logger
from ada.fem import FemSet
from ada.fem.containers import FemSets
from ada.fem.formats.utils import str_to_int

from . import cards

if TYPE_CHECKING:
    from ada import FEM


def get_sets(bulk_str: str, parent: "FEM") -> FemSets:
    set_reader = SetReader(bulk_str, parent)
    return FemSets(set_reader.run(), parent=parent)


@dataclass
class SetReader:
    bulk_str: str
    parent: "FEM"

    _set_type_map: dict = field(default_factory=dict)

    def iter_sets(self) -> Iterable[FemSet]:
        set_map = dict()
        set_groups = (self.get_setmap(m, self.parent) for m in cards.re_setmembs.finditer(self.bulk_str))

        for setid_el_type, content in groupby(sorted(set_groups, key=itemgetter(0, 1)), key=itemgetter(0, 1)):
            setid = setid_el_type[0]
            eltype = setid_el_type[1]
            if setid not in self._set_type_map.keys():
                self._set_type_map[setid] = []
            self._set_type_map[setid].append(eltype)

            set_map[(setid, eltype)] = [list(), eltype]
            for c in content:
                set_map[(setid, eltype)][0] += c[2]

        for m in cards.re_setnames.finditer(self.bulk_str):
            for fs in self.get_femsets(m, set_map, self.parent):
                yield fs

    def run(self) -> list[FemSet]:
        return list(self.iter_sets())

    @staticmethod
    def get_setmap(m, parent):
        d = m.groupdict()
        set_type = "nset" if str_to_int(d["istype"]) == 1 else "elset"
        mem_list = d["members"].split()
        if set_type == "nset":
            members = [parent.nodes.from_id(str_to_int(x)) for x in mem_list]
        else:
            members = [parent.elements.from_id(str_to_int(x)) for x in mem_list]
        return str_to_int(d["isref"]), set_type, members

    def get_femsets(self, m, set_map, parent) -> Iterable[FemSet]:
        d = m.groupdict()
        isref = str_to_int(d["isref"])
        set_name = d["set_name"].strip()
        for set_type in self._set_type_map.get(isref, []):
            try:
                isref_set = set_map[tuple([isref, set_type])]
            except KeyError:
                logger.info(f"Set ID={isref} [{set_name=}] is likely an empty set.")
                isref_set = [[], "nset"]

            yield FemSet(set_name, isref_set[0], isref_set[1], parent=parent)
