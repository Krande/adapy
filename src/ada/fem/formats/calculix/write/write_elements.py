from itertools import groupby
from operator import attrgetter
from typing import Iterable

from ada.config import logger
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
    skipped: dict[str, int] = {}
    unsectioned = 0
    for (el_type, fem_sec), elements in groupby(fem_elements, key=attrgetter("type", "fem_sec")):
        if shape_def.is_structural(el_type) is False:
            # Connectors, masses and springs have no *ELEMENT row in a Calculix deck and
            # no FemSection to size one from — el_type_sub dereferences fem_sec.parent
            # and raises AttributeError on all three. Only connectors were skipped
            # before, so a mass read off a Sesam deck took the writer down with it.
            skipped[str(el_type)] = skipped.get(str(el_type), 0) + sum(1 for _ in elements)
            continue
        if fem_sec is None:
            # A mesh-only deck -- an Abaqus .inp whose elements carry no *SOLID SECTION,
            # say -- reads into elements with no FemSection at all. Calculix takes its
            # element type from the section (``el_type_sub`` reads
            # ``fem_sec.parent.options``), so there is nothing to write one from, and
            # dereferencing None here produced a bare "AttributeError: 'NoneType' object
            # has no attribute 'parent'". Skipped like the rest, and named.
            unsectioned += sum(1 for _ in elements)
            continue
        el_str += elwriter(el_type, fem_sec, elements)

    if skipped:
        logger.warning(
            "calculix writer: skipping %d element(s) Calculix has no element row for: %s. "
            "The rest of the deck is unaffected.",
            sum(skipped.values()),
            ", ".join(f"{k}={v}" for k, v in sorted(skipped.items())),
        )
    if unsectioned:
        logger.warning(
            "calculix writer: skipping %d element(s) with no FemSection -- Calculix takes its "
            "element type from the section, so an unsectioned element cannot be written. Note "
            "the deck keeps any *ELSET / section references to them, so a partial deck needs "
            "checking before it will solve. Bind them to a FemSection on the ada side to avoid "
            "the question.",
            unsectioned,
        )

    if unsectioned and el_str == "":
        # Every structural element lacked a section. Returning "** No elements" here would hand
        # back a deck that reads like a successful conversion of an empty model, which is worse
        # than an error: the model had elements and none of them reached the file.
        #
        # Deliberately scoped to the unsectioned case. A model of only masses / springs /
        # connectors also writes no elements, but that predates this guard and stays as it was:
        # an elementless deck plus the warning above.
        raise IncompatibleElements(
            f"calculix writer: none of the {len(fem_elements)} element(s) could be written -- "
            f"{unsectioned} of them have no FemSection, and Calculix takes its element type from "
            f"the section, so the deck would hold no elements at all."
        )

    return el_str


#: The Abaqus-named element types Calculix implements (CalculiX User's Manual, "Element types")
#: that adapy's shapes map to. A source formulation outside this set has no Calculix form.
CALCULIX_ELEMENT_TYPES = frozenset(
    {
        "B31", "B31R", "B32", "B32R",
        "S3", "S4", "S4R", "S6", "S8", "S8R",
        "M3D3", "M3D4", "M3D4R", "M3D6", "M3D8", "M3D8R",
        "CPS3", "CPS4", "CPS4R", "CPS6", "CPS8", "CPS8R",
        "CPE3", "CPE4", "CPE4R", "CPE6", "CPE8", "CPE8R",
        "CAX3", "CAX4", "CAX4R", "CAX6", "CAX8", "CAX8R",
        "C3D4", "C3D6", "C3D8", "C3D8R", "C3D10", "C3D15", "C3D20", "C3D20R",
    }
)  # fmt: skip


def _calculix_accepts(shape, name: str) -> bool:
    from ada.fem.formats.abaqus.mapping import element_types

    return str(name).upper() in CALCULIX_ELEMENT_TYPES and element_types().accepts(shape, name)


def elwriter(eltype, fem_sec: FemSection, elements: Iterable[Elem]):
    from ada.fem.formulations import resolve

    sub_eltype = el_type_sub(eltype, fem_sec)
    el_set_str = f", ELSET={fem_sec.elset.name}" if fem_sec.elset is not None else ""
    if sub_eltype == "U1":  # a beam of a general section: the section decides, not the element
        by_type = {sub_eltype: list(elements)}
    else:
        # Each element's own type: the caller's rules, else the Abaqus-named type it was read as
        # when Calculix has it, else the default for the section. Every element used to get the
        # default, so an S4R read from an Abaqus deck was written as a fully integrated S4.
        by_type: dict[str, list] = {}
        for el in elements:
            written = resolve(
                el, "calculix", default=lambda _shape: sub_eltype, accepts=_calculix_accepts, stage="calculix writer"
            )
            by_type.setdefault(str(written).upper(), []).append(el)
    return "".join(
        f"""*ELEMENT, type={t}{el_set_str}\n{chr(10).join(write_elem(el) for el in els)}\n"""
        for t, els in by_type.items()
    )


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
