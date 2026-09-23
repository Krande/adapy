from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING, Iterable

import numpy as np

from ada.config import logger
from ada.core.utils import Counter, roundoff
from ada.fem import ConnectorSection, FemSection
from ada.fem.containers import FemSections
from ada.fem.elements import Eccentricity
from ada.fem.shapes import ElemType

from .helper_utils import list_cleanup
from .keywords import validate
from .lexer import Card, comment_property, iter_cards, tokenize

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

    def grab_beam(card: Card):
        validate(card)
        if len(card.data_lines) < 2:
            logger.warning("abaqus read: *Beam Section (line %d) needs two data lines — skipping", card.lineno)
            return None
        elset_name = card.params.get("ELSET")
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
        material = ass.materials.get_by_name(card.params.get("MATERIAL"))
        temperature = card.params.get("TEMPERATURE")
        # The guide's own abbreviation: *Beam Section accepts SECTION= and SECT=.
        section_type = card.params.first("SECTION", "SECT")
        geo_props = card.data_lines[0]
        sec = interpret_section(profile_name, section_type, geo_props)
        if sec is None:
            return None
        beam_y = [float(x.strip()) for x in card.data_lines[1].split(",") if x.strip() != ""]
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

    return filter(lambda x: x is not None, map(grab_beam, iter_cards(bulk_str, "BEAM SECTION")))


def get_solid_sections_from_inp(bulk_str, fem: FEM):
    secnames = Counter(1, "solidsec")
    a = fem.parent.get_assembly()

    def grab_solid(card: Card):
        validate(card)
        # Abaqus/CAE writes the section's name in the comment directly above the card and
        # nowhere else. Reading it from this card's own comments is what keeps one section
        # from inheriting another's name.
        name = comment_property(card, "Section").get("Section") or next(secnames)
        elset = card.params.get("ELSET")
        mat = a.materials.get_by_name(card.params.get("MATERIAL"))
        return FemSection(
            name=name,
            sec_type=ElemType.SOLID,
            elset=elset,
            material=mat,
            parent=fem,
        )

    return map(grab_solid, iter_cards(bulk_str, "SOLID SECTION"))


def get_shell_sections_from_inp(bulk_str, fem: FEM) -> Iterable[FemSection]:
    a = fem.parent.get_assembly()
    sh_name = Counter(1, "sh")
    return filter(
        lambda x: x is not None,
        (get_shell_section(card, sh_name, fem, a) for card in iter_cards(bulk_str, "SHELL SECTION")),
    )


def get_shell_section(card: Card, sh_name, fem: "FEM", a: "Assembly"):
    validate(card)
    if not card.data_lines:
        logger.warning("abaqus read: *Shell Section (line %d) has no data line — skipping", card.lineno)
        return None
    name = next(sh_name)
    elset = fem.sets.get_elset_from_name(card.params.get("ELSET"))

    mat = a.materials.get_by_name(card.params.get("MATERIAL"))
    # Data line: thickness, number of integration points.
    values = [x.strip() for x in card.data_lines[0].split(",")]
    thickness = float(values[0])
    int_points = values[1] if len(values) > 1 else None

    offset = card.params.get("OFFSET")
    if offset is not None:
        # TODO: update this with the latest eccentricity class
        logger.warning("Offset for Shell elements is not yet evaluated")
        for el in elset.members:
            el.eccentricity = Eccentricity(sh_ecc_vector=offset)
    metadata = dict(controls=card.params.get("CONTROLS"))

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


def conn_from_groupdict(d: dict, parent):
    name = d["name"]
    comp = int(d["component"])
    # This does not work reliably
    logger.warning(
        f'Connector section "{name}" has a component number of "{comp}". '
        "Please verify the imported connector, as the connector properties import is not reliable."
    )
    res = np.fromstring(list_cleanup(d["bulk"]), sep=",", dtype=np.float64)
    size = res.size
    cols = comp + 1
    rows = int(size / cols)
    res_ = res.reshape(rows, cols)
    return ConnectorSection(name, [res_], [], metadata=d, parent=parent)


def get_connector_sections_from_bulk(bulk_str: str, parent: FEM = None) -> dict[str, ConnectorSection]:
    """``*Connector Behavior`` plus the ``*Connector Elasticity`` cards that follow it.

    Abaqus nests these by adjacency rather than with an end keyword, so the behaviour owns
    every elasticity card up to the next card that is not one.
    """
    consecsd: dict[str, ConnectorSection] = {}
    cards = tokenize(bulk_str)

    for i, card in enumerate(cards):
        if card.keyword != "CONNECTOR BEHAVIOR":
            continue
        validate(card)
        name = card.params.get("NAME")
        for sub in cards[i + 1 :]:
            if sub.keyword != "CONNECTOR ELASTICITY":
                break
            validate(sub)
            d = dict(
                name=name,
                nonlinear="NONLINEAR" if "NONLINEAR" in sub.params else None,
                component=sub.params.get("COMPONENT"),
                dependencies=sub.params.get("DEPENDENCIES"),
                bulk=sub.data_text,
            )
            consecsd[name] = conn_from_groupdict(d, parent)
    return consecsd
