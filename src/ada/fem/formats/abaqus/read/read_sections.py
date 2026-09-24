from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Iterable

from ada.config import logger
from ada.core.utils import Counter, roundoff
from ada.fem import ConnectorSection, FemSection
from ada.fem.containers import FemSections
from ada.fem.elements import Eccentricity
from ada.fem.shapes import ElemType

from .keywords import validate
from .lexer import KeywordBlock, comment_property, iter_keywords, mark_read, tokenize

part_name_counter = Counter(1, "Part")

if TYPE_CHECKING:
    from ada.api.spatial import Assembly
    from ada.fem import FEM


def get_sections_from_inp(bulk_str, fem: FEM) -> FemSections:
    iter_beams = get_beam_sections_from_inp(bulk_str, fem)
    iter_shell = get_shell_sections_from_inp(bulk_str, fem)
    iter_solid = get_solid_sections_from_inp(bulk_str, fem)

    return FemSections(chain.from_iterable([iter_beams, iter_shell, iter_solid]), fem)


def get_beam_sections_from_inp(bulk_str: str, fem: FEM) -> Iterable[FemSection]:
    # Source:  https://abaqus-docs.mit.edu/2017/English/SIMACAEELMRefMap/simaelm-c-beamcrosssectlib.htm
    from ada import Section
    from ada.sections import GeneralProperties

    ass = fem.parent.get_assembly()

    def interpret_section(profile_name, sec_type, props):
        props_clean = [roundoff(x) for x in filter(lambda x: x.strip() != "", props.split(","))]
        if sec_type.upper() == "BOX":
            b, h, t1, t2, t3, t4 = props_clean
            return Section(
                profile_name,
                "BG",
                h=h,
                w_btn=b,
                w_top=b,
                t_w=t1,
                t_fbtn=t4,
                t_ftop=t2,
                parent=fem,
            )
        elif sec_type.upper() == "CIRC":
            return Section(profile_name, "CIRC", r=props_clean[0], parent=fem)
        elif sec_type.upper() == "I":
            (
                l,
                h,
                b1,
                b2,
                t1,
                t2,
                t3,
            ) = props_clean
            return Section(
                profile_name,
                "IG",
                h=h,
                w_btn=b1,
                w_top=b2,
                t_w=t3,
                t_fbtn=t1,
                t_ftop=t2,
                parent=fem,
            )
        elif sec_type.upper() == "L":
            b, h, t1, t2 = props_clean
            return Section(profile_name, "HP", h=h, w_btn=b, t_w=t2, t_fbtn=t1, parent=fem)
        elif sec_type.upper() == "PIPE":
            r, t = props_clean
            return Section(profile_name, "TUB", r=r, wt=t, parent=fem)
        elif sec_type.upper() == "TRAPEZOID":
            # Currently converts Trapezoid to general beam
            b, h, a, d = props_clean
            # Assuming the Abaqus trapezoid element is symmetrical
            c = (b - a) / 2

            # The properties were quickly copied from a resource online. Most likely it contains error
            # https: // www.efunda.com / math / areas / trapezoidJz.cfm
            genprops = GeneralProperties(
                Ax=h * (a + b) / 2,
                Ix=h
                * (
                    b * h**2
                    + 3 * a * h**2
                    + a**3
                    + 3 * a * c**2
                    + 3 * c * a**2
                    + b**3
                    + c * b**2
                    + a * b**2
                    + b * c**2
                    + 2 * a * b * c
                    + b * a**2
                ),
                Iy=(h**3) * (3 * a + b) / 12,
                Iz=h
                * (a**3 + 3 * a * c**2 + 3 * c * a**2 + b**3 + c * b**2 + a * b**2 + 2 * a * b * c + b * a**2)
                / 12,
            )
            return Section(profile_name, "GENBEAM", genprops=genprops, parent=fem)
        else:
            logger.error(f'Currently unsupported section type "{sec_type}". Will return None')
            return None

    def grab_beam(block: KeywordBlock):
        validate(block)
        if len(block.data_lines) < 2:
            logger.warning("abaqus read: *Beam Section (line %d) needs two data lines — skipping", block.lineno)
            return None
        elset_name = block.params.get("ELSET")
        elset = fem.elsets.get(elset_name)
        if elset is None:
            # The section references an elset that was never created — typically because all
            # its elements are an unsupported type that got skipped during element read (e.g.
            # CalculiX user elements `*ELEMENT, TYPE=U1, ELSET=Eall`). Skip the section
            # rather than KeyError-ing out of the whole import.
            logger.warning(f"Skipping beam section: elset {elset_name!r} not found (elements likely unsupported)")
            return None
        name = elset.name
        profile_name = elset.name
        material = ass.materials.get_by_name(block.params.get("MATERIAL"))
        temperature = block.params.get("TEMPERATURE")
        # The guide's own abbreviation: *Beam Section accepts SECTION= and SECT=.
        section_type = block.params.first("SECTION", "SECT")
        geo_props = block.data_lines[0]
        sec = interpret_section(profile_name, section_type, geo_props)
        if sec is None:
            return None
        beam_y = [float(x.strip()) for x in block.data_lines[1].split(",") if x.strip() != ""]
        metadata = dict(
            temperature=temperature,
            profile=profile_name.strip(),
            section_type=section_type,
            line1=geo_props,
        )
        res = fem.parent.sections.add(sec)
        if res is not None:
            sec = res
        return FemSection(
            name.strip(),
            sec_type=ElemType.LINE,
            elset=elset,
            section=sec,
            local_y=beam_y,
            material=material,
            metadata=metadata,
            parent=fem,
        )

    return filter(lambda x: x is not None, map(grab_beam, iter_keywords(bulk_str, "BEAM SECTION")))


def get_solid_sections_from_inp(bulk_str, fem: FEM):
    secnames = Counter(1, "solidsec")
    a = fem.parent.get_assembly()

    def grab_solid(block: KeywordBlock):
        validate(block)
        # Abaqus/CAE writes the section's name in the comment directly above the block and
        # nowhere else. Reading it from this block's own comments is what keeps one section
        # from inheriting another's name.
        name = comment_property(block, "Section").get("Section") or next(secnames)
        elset = block.params.get("ELSET")
        mat = a.materials.get_by_name(block.params.get("MATERIAL"))
        return FemSection(
            name=name,
            sec_type=ElemType.SOLID,
            elset=elset,
            material=mat,
            parent=fem,
        )

    return map(grab_solid, iter_keywords(bulk_str, "SOLID SECTION"))


def get_shell_sections_from_inp(bulk_str, fem: FEM) -> Iterable[FemSection]:
    a = fem.parent.get_assembly()
    sh_name = Counter(1, "sh")
    return filter(
        lambda x: x is not None,
        (get_shell_section(block, sh_name, fem, a) for block in iter_keywords(bulk_str, "SHELL SECTION")),
    )


def get_shell_section(block: KeywordBlock, sh_name, fem: "FEM", a: "Assembly"):
    validate(block)
    if not block.data_lines:
        logger.warning("abaqus read: *Shell Section (line %d) has no data line — skipping", block.lineno)
        return None
    name = next(sh_name)
    elset = fem.sets.get_elset_from_name(block.params.get("ELSET"))

    mat = a.materials.get_by_name(block.params.get("MATERIAL"))
    # Data line: thickness, number of integration points.
    values = [x.strip() for x in block.data_lines[0].split(",")]
    thickness = float(values[0])
    int_points = values[1] if len(values) > 1 else None

    offset = block.params.get("OFFSET")
    if offset is not None:
        # TODO: update this with the latest eccentricity class
        logger.warning("Offset for Shell elements is not yet evaluated")
        for el in elset.members:
            el.eccentricity = Eccentricity(sh_ecc_vector=offset)
    metadata = dict(controls=block.params.get("CONTROLS"))

    return FemSection(
        name=name,
        sec_type=ElemType.SHELL,
        thickness=thickness,
        elset=elset,
        material=mat,
        int_points=int_points,
        parent=fem,
        metadata=metadata,
    )


#: The blocks that belong to the ``*Connector Behavior`` above them (Abaqus nests these by
#: adjacency, not with an end keyword).
_CONNECTOR_BEHAVIOR_OPTIONS = (
    "CONNECTOR ELASTICITY",
    "CONNECTOR DAMPING",
    "CONNECTOR PLASTICITY",
    "CONNECTOR HARDENING",
)
_DAMPING_KNOWN_PARAMS = {"COMPONENT", "NONLINEAR", "DEPENDENCIES"}


def _floats(line: str) -> list[float]:
    return [float(x) for x in line.split(",") if x.strip()]


def _set_component(comps: list, component: int, value) -> None:
    while len(comps) < component:
        comps.append(None)
    comps[component - 1] = value


def _component_value(block: KeywordBlock):
    """A linear block's one stiffness/coefficient, or a nonlinear block's table of rows."""
    if "NONLINEAR" in block.params:
        return [_floats(line) for line in block.data_lines]
    return _floats(block.data_lines[0])[0]


def get_connector_sections_from_bulk(bulk_str: str, parent: FEM = None) -> dict[str, ConnectorSection]:
    """``*Connector Behavior`` plus every block that belongs to it: elasticity (linear,
    nonlinear or rigid), damping, and plasticity with its hardening table.

    Each property is a list indexed by component - 1, the shape the writer consumes. The
    previous reader kept only the LAST elasticity block of a behaviour, read none of the
    others, and assumed ``component + 1`` values per row, so a linear stiffness did not parse.
    """
    consecsd: dict[str, ConnectorSection] = {}
    mark_read("CONNECTOR BEHAVIOR", *_CONNECTOR_BEHAVIOR_OPTIONS)
    blocks = tokenize(bulk_str)

    for i, block in enumerate(blocks):
        if block.keyword != "CONNECTOR BEHAVIOR":
            continue
        validate(block)
        name = block.params.get("NAME")
        elastic: list = []
        damping: list = []
        plastic: list = []
        rigid = None
        extra_damper_args = ""
        plastic_component = None
        for sub in blocks[i + 1 :]:
            if sub.keyword not in _CONNECTOR_BEHAVIOR_OPTIONS:
                break
            validate(sub)
            component = int(sub.params.get("COMPONENT") or 1)
            if sub.keyword == "CONNECTOR ELASTICITY":
                if "RIGID" in sub.params:
                    rigid = [int(v) for line in sub.data_lines for v in line.split(",") if v.strip()]
                else:
                    _set_component(elastic, component, _component_value(sub))
            elif sub.keyword == "CONNECTOR DAMPING":
                _set_component(damping, component, _component_value(sub))
                extra = [
                    k if v is None else f"{k}={v}" for k, v in sub.params.items() if k not in _DAMPING_KNOWN_PARAMS
                ]
                if extra:
                    extra_damper_args = ", ".join(extra)
            elif sub.keyword == "CONNECTOR PLASTICITY":
                plastic_component = component
            elif sub.keyword == "CONNECTOR HARDENING":
                rows = [tuple(_floats(line)) for line in sub.data_lines]
                _set_component(plastic, plastic_component or 1, rows)

        metadata = {"abaqus": {"extra_damper_args": extra_damper_args}} if extra_damper_args else {}
        consecsd[name] = ConnectorSection(
            name,
            elastic_comp=elastic,
            damping_comp=damping,
            plastic_comp=plastic or None,
            rigid_dofs=rigid,
            metadata=metadata,
            parent=parent,
        )
    return consecsd
