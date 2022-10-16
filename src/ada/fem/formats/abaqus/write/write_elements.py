from __future__ import annotations

from itertools import groupby
from operator import attrgetter
from typing import TYPE_CHECKING, Iterable

from ada.core.utils import NewLine
from ada.fem.shapes import definitions as shape_def

from .helper_utils import get_instance_name
from .write_masses import write_mass_elem

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Elem, FemSet


def elements_str(fem: "FEM", written_on_assembly_level: bool) -> str:
    part_el = fem.elements
    grouping = groupby(part_el, key=attrgetter("type", "elset"))
    if len(fem.elements) == 0:
        return "** No elements"

    return "".join(
        list(
            filter(
                lambda x: x is not None,
                [elwriter(x, elements, fem, written_on_assembly_level) for x, elements in grouping],
            )
        )
    ).rstrip()


def write_elements(
    eltype: shape_def.LineShapes | shape_def.ShellShapes | shape_def.SolidShapes,
    elset: FemSet,
    fem: FEM,
    elements: Iterable[Elem],
    alevel: bool,
):
    el_type = fem.options.ABAQUS.default_elements.get_element_type(eltype)
    el_set_str = f", ELSET={elset.name}" if elset is not None else ""
    el_str = "\n".join((write_elem(el, alevel) for el in elements))
    return f"""*ELEMENT, type={el_type}{el_set_str}\n{el_str}\n"""


def write_elem(el: Elem, alevel: bool) -> str:
    nl = NewLine(10, suffix=7 * " ")
    if len(el.nodes) > 6:
        di = " {}"
    else:
        di = "{:>13}"
    el_str = (
        f"{el.id:>7}, " + " ".join([f"{di.format(get_instance_name(no, alevel))}," + next(nl) for no in el.nodes])[:-1]
    )
    return el_str


def elwriter(eltype_set, elements, fem: "FEM", written_on_assembly_level: bool):
    eltype, elset = eltype_set
    if isinstance(eltype, shape_def.ConnectorTypes):
        return None
    elif isinstance(eltype, shape_def.MassTypes):
        return write_mass_elem(eltype, elset, fem, elements, written_on_assembly_level)
    else:
        return write_elements(eltype, elset, fem, elements, written_on_assembly_level)
