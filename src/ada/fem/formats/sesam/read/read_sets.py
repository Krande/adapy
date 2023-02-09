from itertools import groupby
from operator import itemgetter
from typing import TYPE_CHECKING

from ada.fem import FemSet
from ada.fem.containers import FemSets
from ada.fem.formats.utils import str_to_int

from . import cards

if TYPE_CHECKING:
    from ada import FEM


def get_sets(bulk_str: str, parent: "FEM") -> FemSets:
    set_map = dict()
    set_groups = (get_setmap(m, parent) for m in cards.re_setmembs.finditer(bulk_str))

    for setid_el_type, content in groupby(set_groups, key=itemgetter(0, 1)):
        setid = setid_el_type[0]
        eltype = setid_el_type[1]
        set_map[setid] = [list(), eltype]
        for c in content:
            set_map[setid][0] += c[2]
    fem_sets = [get_femsets(m, set_map, parent) for m in cards.re_setnames.finditer(bulk_str)]
    return FemSets(fem_sets, parent=parent)


def get_setmap(m, parent):
    d = m.groupdict()
    set_type = "nset" if str_to_int(d["istype"]) == 1 else "elset"
    mem_list = d["members"].split()
    if set_type == "nset":
        members = [parent.nodes.from_id(str_to_int(x)) for x in mem_list]
    else:
        members = [parent.elements.from_id(str_to_int(x)) for x in mem_list]
    return str_to_int(d["isref"]), set_type, members


def get_femsets(m, set_map, parent) -> FemSet:
    d = m.groupdict()
    isref = str_to_int(d["isref"])
    fem_set = FemSet(
        d["set_name"].strip(),
        set_map[isref][0],
        set_map[isref][1],
        parent=parent,
    )
    return fem_set
