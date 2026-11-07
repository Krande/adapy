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
* on a beam, an ``end1`` / ``end2`` that says anything (GeniE's end releases and end properties): ``omitted``;
* on a plate, a front or back corrosion addition that is not zero: ``omitted`` (the plate keeps its full thickness).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict

from ada.fem.formats import conversion_report

STAGE = "genie xml reader"

#: The property kinds a reader reads.
READ_PROPERTIES = frozenset({"materials", "sections", "thicknesses", "mass_density_factors"})

#: Property kinds that change the structure's stiffness or mass: left out, they make a different model.
MECHANICAL = {
    "hinges": "beam end releases",
    "corrosion_additions": "corrosion additions (thickness reductions)",
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
    for st in domain.iterfind("structures/structure"):
        for concept in st:
            if concept.tag not in READ_CONCEPTS:
                if not concept.tag.startswith("support"):  # every support kind is read_bcs's to name
                    unread[concept.tag].append(concept)
                continue
            if concept.tag in ("straight_beam", "curved_beam"):
                if any(
                    len(end) or end.attrib for end in (concept.find("end1"), concept.find("end2")) if end is not None
                ):
                    ends.append(concept)
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
            "beam end properties (releases, end hinges) this reader does not read: the beams come in fully connected",
            count=len(ends),
        )
    if corroded:
        rep.omitted(
            STAGE,
            "corrosion_addition_ref",
            ", ".join(_names(corroded)),
            "a plate side's corrosion addition: the plate is read at its full thickness",
            count=len(corroded),
        )


def _is_zero(value) -> bool:
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return False
