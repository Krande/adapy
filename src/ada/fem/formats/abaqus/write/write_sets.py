from itertools import groupby
from operator import attrgetter

from ada.core.utils import NewLine
from ada.fem import FemSet


def aba_set_str(fem_set: FemSet, written_on_assembly_level: bool):
    newline = NewLine(15)

    if len(fem_set.members) == 0:
        if "generate" in fem_set.metadata.keys():
            if fem_set.metadata["generate"] is False:
                raise ValueError(f'set "{fem_set.name}" is empty. Please check your input')
        else:
            raise ValueError("No Members are found")

    generate = fem_set.metadata.get("generate", False)
    internal = fem_set.metadata.get("internal", False)
    if fem_set.parent.options.ABAQUS.inp_format.underline_prefix_is_internal is True:
        if fem_set.name[0] == "_":
            internal = True

    el_str = "*Elset, elset" if fem_set.type == FemSet.TYPES.ELSET else "*Nset, nset"

    el_instances = dict()

    for parent, mem in groupby(fem_set.members, key=attrgetter("parent")):
        el_instances[parent.name] = list(mem)

    set_str = ""
    for elinst, members in el_instances.items():
        el_root = f"{el_str}={fem_set.name}"
        if written_on_assembly_level:
            if internal is True:
                el_root += "" if "," in el_str[-2] else ", "
                el_root += "internal"
            if elinst != fem_set.parent.name:
                el_root += "" if "," in el_str[-2] else ", "
                el_root += f"instance={elinst}"

        if generate is True:
            assert len(fem_set.metadata["gen_mem"]) == 3
            el_root += "" if "," in el_root[-2] else ", "
            set_str += (
                el_root + "generate\n {},  {},   {}" "".format(*[no for no in fem_set.metadata["gen_mem"]]) + "\n"
            )
        else:
            set_str += el_root + "\n " + " ".join([f"{no.id}," + next(newline) for no in members]).rstrip()[:-1] + "\n"
    return set_str.rstrip()
