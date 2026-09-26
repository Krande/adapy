from __future__ import annotations

from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from ada import FEM, Part
    from ada.fem import FemSet, Surface


def list_cleanup(membulkstr):
    return membulkstr.replace(",\n", ",").replace("\n", ",")


def is_set_in_part(part: Part, set_name: str, set_type) -> Union[FemSet, Surface]:
    set_map = {"nset": part.fem.nsets, "elset": part.fem.elsets, "surface": part.fem.surfaces}
    id_map = {"nset": part.fem.nodes, "elset": part.fem.elements}

    if str.isnumeric(set_name):
        _id = int(set_name)
        return id_map[set_type].from_id(_id)

    if set_name in set_map[set_type].keys():
        return set_map[set_type][set_name]

    raise ValueError()


def get_set_from_assembly(set_str: str, fem: "FEM", set_type) -> Union["FemSet", "Surface"]:
    res = set_str.split(".")

    if len(res) == 1:
        local_set_map = {"nset": fem.nsets, "elset": fem.elsets, "surface": fem.surfaces}
        set_name = res[0]
        return local_set_map[set_type][set_name]

    set_name = res[1]
    p_name = res[0]

    if str.isnumeric(set_name):
        num_id = int(set_name)
        local_id_map = {"nset": fem.nodes.from_id, "elset": fem.elements.from_id}
        if p_name == fem.name:
            return local_id_map[set_type](num_id)
        for part in fem.parent.get_all_parts_in_assembly():
            if p_name == part.fem.instance_name:
                r = is_set_in_part(part, set_name, set_type)
                if r is not None:
                    return r
    else:
        local_set_map = {"nset": fem.nsets, "elset": fem.elsets, "surface": fem.surfaces}

        if p_name == fem.name:
            return local_set_map[p_name]
        for part in fem.parent.get_all_parts_in_assembly():
            if p_name == part.fem.instance_name:
                r = is_set_in_part(part, set_name, set_type)
                if r is not None:
                    return r
    raise ValueError(f'No {set_type} "{set_str}" was found')
