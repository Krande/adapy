"""What a GeniE XML holds and this reader does not read, named in the conversion report.

The support and load readers name everything they leave out (``read_bcs``, ``read_loads``); the beams, plates,
materials, sections, masses and sets were read as far as they go and the rest of the file was ignored without a word
-- a model's hinges, corrosion additions or reinforcements came in as if it had none. This walks the file for what no
reader looks at and files each kind once, with how many there are and the first names:

* **properties** (``structure_domain/properties``): each non-empty kind this reader has no field for that changes
  the structure's stiffness or mass (:data:`MECHANICAL`), ``omitted``. The rest -- hydrodynamic, fatigue, meshing
  and design-rule data -- are not named: an FE model has no place for them either way. A corrosion addition of
  zero, GeniE's default ``cThk00``, changes nothing and is not named;
* **concepts** (``structure_domain/structures/structure/*``) of a kind no reader reads;
* on a beam, an ``end1`` / ``end2`` that says anything but its hinge (read by ``read_hinges``): ``omitted``;
* on a beam, an ``aligned_curve_offset`` that says more than its alignment (a nonzero ``constant_value``, a
  ``shiftLocalY``): ``omitted``;
* on a plate side or a beam, a corrosion addition that is not zero: a ``note``. GeniE's own mesh keeps the gross
  thickness and section, as this reads them (measured, V9.3-00), but the addition is not kept for writing back.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict

from ada.fem.formats import conversion_report

STAGE = "genie xml reader"

#: The property kinds a reader reads.
#: ``hinges``: read_hinges, which names any it cannot hold. ``corrosion_additions``: GeniE's own mesh is the same
#: without them (below, :func:`report_unread`), so they are named as a note, not as an omission.
READ_PROPERTIES = frozenset(
    {"materials", "sections", "thicknesses", "mass_density_factors", "hinges", "corrosion_additions"}
)

#: Property kinds that change the structure's stiffness or mass: left out, they make a different model.
MECHANICAL = {
    "thickness_corrections": "thickness corrections",
    "effective_flange": "effective flange widths",
    "reinforcements": "reinforcements",
    "beam_types": "beam types",
    "plate_types": "plate types",
}

#: The concept kinds a reader reads.
READ_CONCEPTS = frozenset(
    {
        "straight_beam",
        "curved_beam",
        "flat_plate",
        "curved_shell",
        "point_mass",
        "support_point",
        "support_curve",
        "support_rigid_link",
    }
)


def _names(items) -> list[str]:
    return [it.get("name") or it.tag for it in items][:10]


def report_unread(xml_root: ET.Element) -> None:
    rep = conversion_report.current()
    domain = xml_root.find("./model/structure_domain")
    if domain is None:
        return
    zero_corrosion = set()
    props = domain.find("properties")
    for kind in list(props) if props is not None else []:
        items = list(kind)
        if kind.tag == "corrosion_additions":
            zero_corrosion = {it.get("name") for it in items if _is_zero(it.get("addition"))}
            items = [it for it in items if it.get("name") not in zero_corrosion]
        what = MECHANICAL.get(kind.tag)
        if kind.tag in READ_PROPERTIES or not items or what is None:
            continue
        rep.omitted(
            STAGE,
            kind.tag,
            ", ".join(_names(items)),
            f"{what}: this reader has no field for them, so the model is read without them",
            count=len(items),
        )

    unread = defaultdict(list)
    ends = []
    corroded = []
    aligned_extra = []
    for st in domain.iterfind("structures/structure"):
        for concept in st:
            if concept.tag not in READ_CONCEPTS:
                if not concept.tag.startswith("support"):  # every support kind is read_bcs's to name
                    unread[concept.tag].append(concept)
                continue
            if concept.tag in ("straight_beam", "curved_beam"):
                # an end's hinge_ref is read (read_hinges); anything else on it is not
                if any(
                    len(end) or set(end.attrib) - {"hinge_ref"}
                    for end in (concept.find("end1"), concept.find("end2"))
                    if end is not None
                ):
                    ends.append(concept)
                ref = concept.get("corrosion_addition_ref")
                if ref and ref not in zero_corrosion:
                    corroded.append(concept)
                # read_beams reads an aligned offset's ``alignment`` only; a nonzero ``constant_value``, a
                # ``shiftLocalY`` or anything else on it moves the beam where the model read does not put it
                aligned = concept.find("curve_offset/aligned_curve_offset")
                if aligned is not None and (
                    len(aligned)
                    or set(aligned.attrib) - {"alignment", "constant_value"}
                    or not _is_zero(aligned.get("constant_value", "0"))
                ):
                    aligned_extra.append(concept)
            if concept.tag in ("flat_plate", "curved_shell"):
                for side in ("front", "back"):
                    el = concept.find(side)
                    ref = None if el is None else el.get("corrosion_addition_ref")
                    if ref and ref not in zero_corrosion:
                        corroded.append(concept)
                        break
    for tag, concepts in sorted(unread.items()):
        rep.omitted(
            STAGE, tag, ", ".join(_names(concepts)), "a concept kind this reader does not read", count=len(concepts)
        )
    if ends:
        rep.omitted(
            STAGE,
            "end1/end2",
            ", ".join(_names(ends)),
            "beam end properties other than an end hinge, which this reader does not read",
            count=len(ends),
        )
    if aligned_extra:
        rep.omitted(
            STAGE,
            "aligned_curve_offset",
            ", ".join(_names(aligned_extra)),
            "an aligned curve offset with a nonzero constant_value, a shiftLocalY or other values besides its "
            "alignment, which this reader does not read: the beam is placed by its alignment alone",
            count=len(aligned_extra),
        )
    if corroded:
        # Measured, GeniE V9.3-00: a plate with 2 mm on its front and 3 mm on its back meshes to the gross GELTH, and a
        # beam with 2 mm to the gross section (tests/fem/test_genie_gxml_hinges.py) -- GeniE keeps corrosion for its
        # code checks, not its FE model. So the model read is the one GeniE meshes; the additions are not kept.
        rep.note(
            STAGE,
            "corrosion_addition_ref",
            ", ".join(_names(corroded)),
            "a corrosion addition, which GeniE's own mesh leaves at the gross thickness and section, as this reads them; "
            "the addition itself is not kept for writing back",
            count=len(corroded),
        )


def _is_zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False
