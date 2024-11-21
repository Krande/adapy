import xml.etree.ElementTree as ET

import ada


def add_sets(structure_domain, part: ada.Part):
    sets = ET.SubElement(structure_domain, "sets")
    for p in part.get_all_parts_in_assembly(include_self=True):
        for group in p.groups.values():
            set_elem = ET.SubElement(sets, "set", name=group.name)
            concepts = ET.SubElement(set_elem, "concepts")
            for member in group.members:
                ET.SubElement(concepts, "concept", concept_ref=member.name)
