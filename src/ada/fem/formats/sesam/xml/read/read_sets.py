import xml.etree.ElementTree as ET
from typing import Dict, Union

from ada import Beam, Group, Part
from ada.config import get_logger

logger = get_logger()


def get_sets(xml_root: ET.Element, parent: Part) -> Dict[str, Group]:
    el_sets = dict()
    for el_set in xml_root.findall(".//set"):
        name = el_set.attrib["name"]
        members = list(filter(lambda x: x is not None, [get_concept(m, parent) for m in el_set.findall(".//concept")]))
        el_sets[name] = Group(name, members, parent=parent)

    return el_sets


def get_concept(xml_el: ET.Element, part: Part) -> Union[Beam]:
    ref = xml_el.attrib["concept_ref"]
    if ref in part.beams.dmap.keys():
        return part.beams.from_name(ref)
    else:
        logger.debug(f'Currently only Beams are supported. Unable to find group member "{ref}"')
