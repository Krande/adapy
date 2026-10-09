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


def elements_str(fem_elements: FemElements, report_locking: bool = True) -> str:
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
        el_str += elwriter(el_type, fem_sec, elements, report_locking)

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


def elwriter(eltype, fem_sec: FemSection, elements: Iterable[Elem], report_locking: bool = True):
    from ada.fem.formulations import resolve

    sub_eltype = el_type_sub(eltype, fem_sec)
    el_set_str = f", ELSET={fem_sec.elset.name}" if fem_sec.elset is not None else ""
    if isinstance(eltype, shape_def.LineShapes):  # a beam: the section decides (beam_element_type), not the element
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
        for t in sorted(by_type):
            if report_locking and t in LOCKING_SHELLS:
                from ada.fem.formats import conversion_report

                conversion_report.current().approximated(
                    "calculix writer",
                    "*ELEMENT",
                    fem_sec.elset.name,
                    f"written as {t}, which {LOCKING_SHELLS[t]}; a plate solved with it is far too stiff. Mesh plates "
                    f"with quads (S4) or second-order elements (S6, S8) for CalculiX",
                    element_type=t,
                    n_elements=len(by_type[t]),
                )
    return "".join(
        f"""*ELEMENT, type={t}{el_set_str}
{chr(10).join(write_elem(el) for el in els)}
"""
        for t, els in by_type.items()
    )


#: Shell elements this writer reports, and why: each is expanded by ccx 2.23 into one layer of bricks that locks on a
#: plate. They are still written -- an S3 or S4R read from another deck is that element, and CalculiX has no other
#: three-node shell to put in its place -- with an ``approximated`` finding carrying the measurement. Measured on a 4.0 x 0.5 m, 10 mm plate at 0.125 / 0.0625 m, against Code_Aster DKT at 0.03125 m (cantilever
#: under gravity, -1.38452 m) and 5 q L^4 / (384 D) (strip under 1000 Pa, -0.173333 m): S4R (C3D8R) gave -0.0741 /
#: -0.2857 m and -0.0100 / -0.0386 m, S3 (C3D6) -0.233 / -0.414 m and -0.0118 / -0.0380 m -- where S4 gave
#: -1.36346 / -1.3807 m and -0.173065 / -0.173268 m, and S6, S8 and S8R were within 1 %. ccx's own three-node shell, the
#: US3 user element, stops this ccx 2.23 build with a segmentation fault on a two-element deck.
LOCKING_SHELLS = {
    "S4R": "CalculiX expands into a single C3D8R layer that locks on a plate (-95 % on a cantilever plate)",
    "S3": "CalculiX expands into a single C3D6 layer that locks on a plate (-83 % on a cantilever plate); its US3 "
    "triangle crashes ccx 2.23",
}

#: CalculiX's two-node Timoshenko user element, written for every two-node beam (see :func:`beam_element_type`).
U1 = "U1"
#: The three-node beam CalculiX expands into a C3D20R brick; its BOX and PIPE sections exist on no other element.
B32R = "B32R"


def beam_element_type(el_type, fem_sec: FemSection) -> str:
    """The CalculiX element a beam of this shape and section is written as: ``U1`` for a two-node beam, ``B32R`` for
    a three-node beam of a box or pipe section; any other three-node beam is refused.

    Measured with ccx 2.23 (``CALCULIX_CODE_ASTER.md``):

    * ``B31``, ``B32`` and ``B32R`` take a geometric section (RECT, CIRC, PIPE, BOX) and are expanded into C3D8I /
      C3D20 / C3D20R bricks; ``SECTION=BOX`` and ``SECTION=PIPE`` stop ccx at "can only be used for B32R elements" on
      the other two. adapy wrote them on B31 (and B32), so no box or pipe beam deck of it ever ran.
    * An I, T, angle or general section has no geometric form at all, so a two-node beam of any section goes out as
      ``U1`` with ``SECTION=GENERAL`` (area, inertias, orientation). That also covers a box or pipe on a two-node mesh,
      which has no B32R middle node.
    * ``U1`` is exact Euler-Bernoulli bending at any mesh when its shear coefficient is large (see
      :data:`ada.fem.formats.calculix.write.writer.U1_SHEAR_COEFFICIENT`), where the expanded bricks are a 3D solid
      answer -- B32R on a 0.2 x 0.4 box, simply supported, 4 m, 1 kN at mid-span: +3.8 % on Euler-Bernoulli and
      -2.3 % on Timoshenko at 4, 8 and 16 elements alike.
    """
    from ada.sections.categories import BaseTypes

    if el_type == shape_def.LineShapes.LINE:
        return U1
    if fem_sec.section.type in (BaseTypes.BOX, BaseTypes.TUBULAR):
        return B32R
    raise IncompatibleElements(
        f"calculix writer: a three-node beam of a {fem_sec.section.type} section ({fem_sec.elset.name}) has no "
        f"CalculiX form -- U1 has two nodes, and B32R takes a RECT, CIRC, PIPE or BOX outline only. Mesh it with "
        f"two-node beams."
    )


def el_type_sub(el_type, fem_sec: FemSection) -> str:
    """Substitute Element types specifically Calculix"""

    if isinstance(el_type, shape_def.LineShapes):
        return beam_element_type(el_type, fem_sec)
    fem = fem_sec.parent
    if el_type == ElemShape.TYPES.shell.TRI6:
        if fem.options.CALCULIX.default_elements.use_reduced_integration:
            raise IncompatibleElements(f"Reduced integration is not supported for triangle elements {el_type}")
        return "S6"

    default_elem = fem.options.CALCULIX.default_elements.get_element_type(el_type)
    return default_elem


def is_u1(elem: Elem) -> bool:
    """Whether ``elem`` is written as a ``U1`` beam."""
    return elem.type == shape_def.LineShapes.LINE and elem.fem_sec is not None


def must_be_converted_to_general_section(sec_type):
    """True for a section CalculiX has no ``*BEAM SECTION`` keyword for.

    CalculiX' beam library is ``RECT``, ``CIRC``, ``PIPE``, ``BOX`` and ``GENERAL``; anything else
    goes out as a general section on a ``U1`` element, carrying its integrated properties instead of
    its outline. ``TPROFILE`` is here for the same reason ``IPROFILE`` is -- a T is an I with one
    flange, and CalculiX has a keyword for neither.
    """
    from ada.sections.categories import BaseTypes

    if sec_type in [
        BaseTypes.CIRCULAR,
        BaseTypes.IPROFILE,
        BaseTypes.TPROFILE,
        BaseTypes.GENERAL,
        BaseTypes.ANGULAR,
    ]:
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
