import xml.etree.ElementTree as ET

from ada import Assembly, Part

from . import get_beams, get_joints, get_materials, get_plates, get_sections
from .read_bcs import get_boundary_conditions
from .read_masses import get_masses
from .read_sets import get_sets


def from_xml_file(xml_path, extract_joints=False, skip_beams=False, skip_plates=False, name=None) -> Assembly:
    print(f'Beginning importing Genie XML from "{xml_path}"')
    root = ET.parse(str(xml_path)).getroot()
    model = root.find(".//model")

    p = Part(model.attrib["name"] if name is None else name)
    p._sections = get_sections(root, p)
    p._materials = get_materials(root, p)
    if skip_beams is False:
        p._beams = get_beams(root, p)
    if skip_plates is False:
        p._plates = get_plates(root, p)
    p._groups = get_sets(root, p)
    for bm in p.beams:
        p.nodes.add(bm.n1)
        p.nodes.add(bm.n2)
    if extract_joints is True:
        p._connections = get_joints(root, p)

    p.fem.bcs += get_boundary_conditions(root, p)
    p.fem.masses.update(get_masses(root, p))

    all_plates = len(p.plates)
    all_beams = len(p.beams)
    all_joints = len(p.connections)
    print(f"Finished importing Genie XML (beams={all_beams}, plates={all_plates}, joints={all_joints})")
    return Assembly(name=model.attrib["name"] if name is None else name) / p
