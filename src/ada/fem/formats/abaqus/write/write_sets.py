from itertools import groupby
from operator import attrgetter
from typing import TYPE_CHECKING

from ada.core.utils import NewLine
from ada.fem import FemSet

from .helper_utils import is_connector_set

if TYPE_CHECKING:
    from ada import FEM


def _is_nonstructural_mass_set(fem_set: FemSet) -> bool:
    """The one-member set adapy keeps for a nonstructural mass's pseudo-element: there is no
    Abaqus element to put in it (the mass is written against the structural set it spreads over)."""
    from ada.fem import Mass

    members = fem_set.members
    return bool(members) and all(isinstance(m, Mass) and m.type == Mass.TYPES.NONSTRU for m in members)


def elsets_str(fem: "FEM", written_on_assembly_level: bool):
    if len(fem.elsets) == 0:
        return "** No element sets"
    return "\n".join(
        [
            aba_set_str(el, written_on_assembly_level)
            for el in fem.elsets.values()
            if not (is_connector_set(el) or _is_nonstructural_mass_set(el))
        ]
    ).rstrip()


def nsets_str(fem: "FEM", written_on_assembly_level: bool):
    if len(fem.nsets) == 0:
        return "** No node sets"
    return "\n".join([aba_set_str(no, written_on_assembly_level) for no in fem.nsets.values()]).rstrip()


def aba_set_str(fem_set: FemSet, written_on_assembly_level: bool, is_ref_point_set=False):
    newline = NewLine(15)

    if len(fem_set.members) == 0 and not fem_set.metadata.get("generate", False):
        # An empty set is an Abaqus construct in its own right -- the guide sets parameters "equal
        # to an empty element set" for Abaqus to fill (generated fastener connectors, explicit
        # domain decomposition). Written as a keyword line with no data lines; it used to be
        # dropped with an error, or refused outright for a set read from a deck.
        kind = "*Elset, elset" if fem_set.type == FemSet.TYPES.ELSET else "*Nset, nset"
        return f"{kind}={fem_set.name}"

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
        name = fem_set.name
        if is_ref_point_set is True:
            name += "-RefPt_"
        el_root = f"{el_str}={name}"
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
