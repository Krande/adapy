from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Dict

from ada import Group, Part
from ada.base.changes import ChangeAction
from ada.config import logger

if TYPE_CHECKING:
    from ada import Beam, Plate


def get_sets(xml_root: ET.Element, parent: Part) -> Dict[str, Group]:
    el_sets = dict()
    for el_set in xml_root.findall(".//set"):
        name = el_set.attrib["name"]
        members = list(filter(lambda x: x is not None, [get_concept(m, parent) for m in el_set.findall(".//concept")]))
        el_sets[name] = Group(name, members, parent=parent, change_type=ChangeAction.ADDED)

    return el_sets


def get_concept(xml_el: ET.Element, part: Part) -> Beam | Plate:
    ref = xml_el.attrib["concept_ref"]
    if ref in part.beams.idmap.keys():
        return part.beams.from_name(ref)
    elif ref in part.plates.idmap.keys():
        return part.plates.from_name(ref)
    else:
        logger.debug(f'Currently only Beams are supported. Unable to find group member "{ref}"')
