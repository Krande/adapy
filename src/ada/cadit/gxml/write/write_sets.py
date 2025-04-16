import xml.etree.ElementTree as ET
from collections import defaultdict

import ada


def add_sets(structure_domain, part: ada.Part):
    sets = ET.SubElement(structure_domain, "sets")

    merged_sets_by_name = defaultdict(list)
    for p in part.get_all_parts_in_assembly(include_self=True):
        for group in p.groups.values():
            merged_sets_by_name[group.name].append(group)

    for group_name, groups in merged_sets_by_name.items():
        set_elem = ET.SubElement(sets, "set", name=group_name)
        concepts = ET.SubElement(set_elem, "concepts")
        for group in groups:
            for member in group.members:
                ET.SubElement(concepts, "concept", concept_ref=member.name)
