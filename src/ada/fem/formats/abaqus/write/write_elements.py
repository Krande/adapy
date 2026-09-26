from __future__ import annotations

from itertools import groupby
from typing import TYPE_CHECKING, Iterable

from ada.core.utils import NewLine
from ada.fem.shapes import definitions as shape_def

from ..grammar import render_keyword
from ..mapping import element_types
from .helper_utils import get_instance_name, set_name
from .write_masses import write_mass_elem

if TYPE_CHECKING:
    from ada import FEM
    from ada.fem import Elem, FemSet


def elements_str(fem: "FEM", written_on_assembly_level: bool) -> str:
    part_el = fem.elements
    # Grouped by the Abaqus TYPE too, not only the shape: rows read as S3 and as CPS3 share the
    # triangle shape but are different elements, and each needs its own *Element block.
    types = element_types()
    defaults = fem.options.ABAQUS.default_elements

    def key(el):
        abaqus_type = None
        if not isinstance(el.type, (shape_def.ConnectorTypes, shape_def.SpringTypes, shape_def.MassTypes)):
            abaqus_type = types.write_type(el, defaults)
        return el.type, abaqus_type, el.elset

    grouping = groupby(part_el, key=key)
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
    abaqus_type: str | None = None,
):
    el_type = abaqus_type or fem.options.ABAQUS.default_elements.get_element_type(eltype)
    params = [("type", el_type)] + ([("ELSET", set_name(elset))] if elset is not None else [])
    return render_keyword("ELEMENT", params, [write_elem(el, alevel) for el in elements])


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
    eltype, abaqus_type, elset = eltype_set
    if isinstance(eltype, shape_def.ConnectorTypes):
        return None
    elif isinstance(eltype, shape_def.SpringTypes):
        # springs_str writes the *Spring cards for these. Emitting them here as well
        # would define every spring twice in the deck — the same reason connectors
        # return None and are left to write_connectors.
        return None
    elif eltype == shape_def.MassTypes.NONSTRUCTURAL:
        # No Abaqus element: *Nonstructural Mass applies to existing elements. This wrote
        # "*ELEMENT, type=NONSTRUCTURAL", a type Abaqus does not have.
        return None
    elif isinstance(eltype, shape_def.MassTypes):
        return write_mass_elem(eltype, elset, fem, elements, written_on_assembly_level)
    else:
        return write_elements(eltype, elset, fem, elements, written_on_assembly_level, abaqus_type)
