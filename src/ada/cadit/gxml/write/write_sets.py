import xml.etree.ElementTree as ET

import ada


def add_sets(structure_domain, part: ada.Part):
    sets = ET.SubElement(structure_domain, "sets")

    merged_sets_by_name = part.get_all_groups_as_merged()

    for group_name, groups in merged_sets_by_name.items():
        set_elem = ET.SubElement(sets, "set", name=group_name)
        concepts = ET.SubElement(set_elem, "concepts")
        for group in groups:
            for member in group.members:
                ET.SubElement(concepts, "concept", concept_ref=member.name)
