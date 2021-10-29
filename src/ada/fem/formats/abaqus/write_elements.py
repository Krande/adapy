from itertools import groupby
from operator import attrgetter
from typing import TYPE_CHECKING

from ada.core.utils import NewLine

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Elem


def elements_str(fem: "FEM") -> str:
    part_el = fem.elements
    grouping = groupby(part_el, key=attrgetter("type", "elset"))
    return (
        "".join([els for els in [elwriter(x, elements, fem) for x, elements in grouping] if els is not None]).rstrip()
        if len(fem.elements) > 0
        else "** No elements"
    )


def aba_write(el: "Elem"):
    nl = NewLine(10, suffix=7 * " ")
    if len(el.nodes) > 6:
        di = " {}"
    else:
        di = "{:>13}"
    return f"{el.id:>7}, " + " ".join([f"{di.format(no.id)}," + next(nl) for no in el.nodes])[:-1]


def elwriter(eltype_set, elements, fem: "FEM"):

    if "connector" in eltype_set:
        return None

    eltype, elset = eltype_set
    el_type = fem.options.ABAQUS.default_elements.get_element_type(eltype)

    el_set_str = f", ELSET={elset.name}" if elset is not None else ""
    el_str = "\n".join(map(aba_write, elements))
    return f"""*ELEMENT, type={el_type}{el_set_str}\n{el_str}\n"""
