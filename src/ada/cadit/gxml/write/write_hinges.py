"""adapy's beam end hinges (:class:`ada.BeamHinge`) as GeniE's ``properties/hinges``, in the beam's local axes.

A hinge of fixed and free dofs only is GeniE's ``connectivity_hinge`` (six ``f`` flags, 1 connected), which GeniE
meshes to ``BELFIX`` OPT 3; one with a spring is a ``flexible_hinge`` (``BELFIX`` OPT 4). Both are what GeniE writes
itself for ``Hinge(...)`` and ``FlexibleHinge(...)`` (``read/read_hinges.py``), so a hinge read from GeniE is written
back as the kind it was and meshes to the same record.
"""

import xml.etree.ElementTree as ET

import ada

_DOFS = ("dx", "dy", "dz", "rx", "ry", "rz")


def add_hinges(props_elem: ET.Element, part: ada.Part) -> None:
    hinges_elem = props_elem.find("./hinges")
    if hinges_elem is None:
        hinges_elem = ET.SubElement(props_elem, "hinges")

    unique_hinges: dict[str, ada.BeamHinge] = {}
    for bm in part.get_all_physical_objects(by_type=ada.Beam):
        for hinge in (bm.hinge1, bm.hinge2):
            if hinge is not None:
                unique_hinges.setdefault(hinge.name, hinge)

    # by name, so the file does not depend on set order
    for name in sorted(unique_hinges):
        hinge_elem = ET.SubElement(hinges_elem, "hinge", {"name": name})
        _hinge_kind(hinge_elem, unique_hinges[name])


def _hinge_kind(hinge_elem: ET.Element, hinge: "ada.BeamHinge") -> None:
    by_dof = {d.dof: d for d in hinge.dofs}
    constraints = {by_dof[d].constraint_type if d in by_dof else "fixed" for d in _DOFS}
    if constraints <= {"fixed", "free"}:
        conn = ET.SubElement(hinge_elem, "connectivity_hinge", {"coordinate_system_type": "local"})
        flags = ET.SubElement(conn, "connectivities")
        for d in _DOFS:
            free = d in by_dof and by_dof[d].constraint_type == "free"
            ET.SubElement(flags, "connectivity", {"f": "0" if free else "1"})
        return
    flexible_hinge = ET.SubElement(hinge_elem, "flexible_hinge", {"coordinate_system_type": "local"})
    stiffness_elem = ET.SubElement(flexible_hinge, "stiffness")
    for d in _DOFS:
        dof = by_dof.get(d)
        attribs = {"dof": d, "constraint": dof.constraint_type if dof is not None else "fixed"}
        if dof is not None and dof.constraint_type == "spring":
            attribs["stiffness"] = repr(float(dof.spring_stiffness))
        ET.SubElement(stiffness_elem, "boundary_condition", attribs)
