from itertools import groupby
from operator import attrgetter
from typing import Iterable

from ada.core.utils import NewLine
from ada.fem import Elem, FemSection
from ada.fem.containers import FemElements
from ada.fem.exceptions import IncompatibleElements
from ada.fem.shapes import ElemShape
from ada.fem.shapes import definitions as shape_def


def elements_str(fem_elements: FemElements) -> str:
    if len(fem_elements) == 0:
        return "** No elements"

    el_str = ""
    for (el_type, fem_sec), elements in groupby(fem_elements, key=attrgetter("type", "fem_sec")):
        if isinstance(el_type, shape_def.ConnectorTypes):
            continue
        el_str += elwriter(el_type, fem_sec, elements)

    return el_str


def elwriter(eltype, fem_sec: FemSection, elements: Iterable[Elem]):
    sub_eltype = el_type_sub(eltype, fem_sec)
    el_set_str = f", ELSET={fem_sec.elset.name}" if fem_sec.elset is not None else ""
    el_str = "\n".join((write_elem(el) for el in elements))

    return f"""*ELEMENT, type={sub_eltype}{el_set_str}\n{el_str}\n"""


def el_type_sub(el_type, fem_sec: FemSection) -> str:
    """Substitute Element types specifically Calculix"""

    if isinstance(el_type, shape_def.LineShapes):
        if must_be_converted_to_general_section(fem_sec.section.type):
            return "U1"
    fem = fem_sec.parent
    if el_type == ElemShape.TYPES.shell.TRI6:
        if fem.options.CALCULIX.default_elements.use_reduced_integration:
            raise IncompatibleElements(f"Reduced integration is not supported for triangle elements {el_type}")
        return "S6"

    default_elem = fem.options.CALCULIX.default_elements.get_element_type(el_type)
    return default_elem


def must_be_converted_to_general_section(sec_type):
    from ada.sections.categories import BaseTypes

    if sec_type in [BaseTypes.CIRCULAR, BaseTypes.IPROFILE, BaseTypes.GENERAL, BaseTypes.ANGULAR]:
        return True
    else:
        return False


def write_elem(el: Elem) -> str:
    nl = NewLine(10, suffix=7 * " ")
    if len(el.nodes) > 6:
        di = " {}"
    else:
        di = "{:>13}"
    el_str = f"{el.id:>7}, " + " ".join([f"{di.format(no.id)}," + next(nl) for no in el.nodes])[:-1]
    return el_str
