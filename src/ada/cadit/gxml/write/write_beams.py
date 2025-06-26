from __future__ import annotations

import itertools
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.api.spatial.equipment import Equipment, EquipRepr
from ada.cadit.sat.write.writer import SatWriter

from .write_utils import add_local_system

if TYPE_CHECKING:
    from ada import Beam, Part


def add_beams(root: ET.Element, part: Part, sw: SatWriter = None):
    from ada import Beam, BeamTapered

    iter_beams = part.get_all_physical_objects(by_type=Beam)
    iter_taper = part.get_all_physical_objects(by_type=BeamTapered)

    for beam in itertools.chain(iter_beams, iter_taper):
        parent = beam.parent
        if isinstance(parent, Equipment) and parent.eq_repr != EquipRepr.AS_IS:
            continue
        add_straight_beam(beam, root)


def add_straight_beam(beam: Beam, xml_root: ET.Element):
    import numpy as np

    from ada import Placement

    structure_elem = ET.SubElement(xml_root, "structure")
    straight_beam = ET.SubElement(structure_elem, "straight_beam", {"name": beam.name})

    xvec = beam.xvec
    yvec = beam.yvec
    up = beam.up

    if beam.placement.is_identity() is False:
        ident_place = Placement()
        place_abs = beam.placement.get_absolute_placement(include_rotations=True)
        place_abs_rot_mat = place_abs.rot_matrix
        ident_rot_mat = ident_place.rot_matrix
        # check if the 3x3 rotational np arrays are identical
        if not np.allclose(place_abs_rot_mat, ident_rot_mat):
            ori_vectors = place_abs.transform_array_from_other_place(
                np.asarray([xvec, yvec, up]), ident_place, ignore_translation=True
            )
            xvec = ori_vectors[0]
            yvec = ori_vectors[1]

    straight_beam.append(add_local_system(xvec, yvec, up))
    straight_beam.append(add_segments(beam))
    if beam.hinge1 is not None:
        ET.SubElement(straight_beam, "end1", {"hinge_ref": beam.hinge1.name})
    if beam.hinge2 is not None:
        ET.SubElement(straight_beam, "end2", {"hinge_ref": beam.hinge2.name})

    curve_offset = ET.SubElement(straight_beam, "curve_offset")
    ET.SubElement(curve_offset, "reparameterized_beam_curve_offset")


def add_curve_orientation(beam: Beam, straight_beam: ET.Element):
    curve_orientation = ET.SubElement(straight_beam, "curve_orientation")
    cco = ET.SubElement(curve_orientation, "customizable_curve_orientation", {"use_default_rule": "true"})
    orientation = ET.SubElement(cco, "orientation")
    local_system = ET.SubElement(orientation, "local_system")
    ET.SubElement(local_system, "x_vector", {"x": str(beam.xvec[0]), "y": str(beam.xvec[1]), "z": str(beam.xvec[2])})
    ET.SubElement(local_system, "y_vector", {"x": str(beam.yvec[0]), "y": str(beam.yvec[1]), "z": str(beam.yvec[2])})
    ET.SubElement(local_system, "up_vector", {"x": str(beam.up[0]), "y": str(beam.up[1]), "z": str(beam.up[2])})


def add_segments(beam: Beam):
    import numpy as np

    from ada import BeamTapered, Placement

    segments = ET.Element("segments")
    props = dict(index="1", section_ref=beam.section.name, material_ref=beam.material.name)
    if isinstance(beam, BeamTapered):
        props.update(dict(section_ref=f"{beam.section.name}_{beam.taper.name}"))

    straight_segment = ET.SubElement(segments, "straight_segment", props)

    d = ["x", "y", "z"]
    p1 = beam.n1.p
    p2 = beam.n2.p
    if beam.placement.is_identity() is False:
        ident_place = Placement()
        place_abs = beam.placement.get_absolute_placement(include_rotations=True)
        place_abs_rot_mat = place_abs.rot_matrix
        ident_rot_mat = ident_place.rot_matrix
        # check if the 3x3 rotational np arrays are identical
        if not np.allclose(place_abs_rot_mat, ident_rot_mat):
            tra_vectors = place_abs.transform_array_from_other_place(np.asarray([p1, p2]), ident_place)
            p1 = tra_vectors[0]
            p2 = tra_vectors[1]
        else:
            p1 = place_abs.origin + p1
            p2 = place_abs.origin + p2

    geom = ET.SubElement(straight_segment, "geometry")
    wire = ET.SubElement(geom, "wire")
    guide = ET.SubElement(wire, "guide")
    for i, pos in enumerate([p1, p2], start=1):
        props = {d[i]: str(k) for i, k in enumerate(pos)}
        props.update(dict(end=str(i)))
        ET.SubElement(guide, "position", props)

    ET.SubElement(wire, "sat_reference")

    # TODO: add SAT embedded geometry and include the reference to the EDGE geometry here
    # ET.SubElement(sat_ref, "edge_ref", dict(edge_ref=""))

    return segments
