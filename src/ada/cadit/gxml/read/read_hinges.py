"""GeniE's beam end hinges (``properties/hinges``) as adapy's :class:`~ada.BeamHinge`, on the beam ends that name them.

A GeniE XML holds two kinds, both in the beam's local axes (``coordinate_system_type="local"``), and a beam end names
one by ``<end1 hinge_ref="..."/>`` / ``<end2 .../>``:

* ``connectivity_hinge``: six ``<connectivity f="1|0"/>``, dx dy dz rx ry rz -- 1 connected to the joint, 0 free
  (GeniE's ``Hinge(1, 1, 1, 1, 0, 0)``). Meshed, it is ``BELFIX`` OPT 3 with those flags;
* ``flexible_hinge``: one ``<boundary_condition dof constraint [stiffness]/>`` per dof, ``fixed``, ``free`` or
  ``spring`` (GeniE's ``FlexibleHinge(Fixed, ..., Stiffness(k))``). Meshed, ``BELFIX`` OPT 4: -1 for fixed, the
  stiffness of a spring.

Measured, GeniE V9.3-00 (``tests/fem/test_genie_gxml_hinges.py``). A hinge in any other frame, or of a kind this does
not know, is left off its beams and reported by name.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ada.api.beams.base_bm import BeamHinge, BeamHingeDofType
from ada.fem.formats import conversion_report

STAGE = "genie xml reader"
_DOFS = ("dx", "dy", "dz", "rx", "ry", "rz")


def _connectivity(name: str, el: ET.Element) -> BeamHinge | None:
    flags = [c.get("f") for c in el.iter("connectivity")]
    if len(flags) != 6 or any(f not in ("0", "1") for f in flags):
        return None
    return BeamHinge(name, [BeamHingeDofType(d, "fixed" if f == "1" else "free") for d, f in zip(_DOFS, flags)])


def _flexible(name: str, el: ET.Element) -> BeamHinge | None:
    by_dof = {bc.get("dof"): bc for bc in el.iter("boundary_condition")}
    if set(by_dof) != set(_DOFS):
        return None
    dofs = []
    for d in _DOFS:
        bc = by_dof[d]
        constraint = bc.get("constraint")
        if constraint == "spring":
            dofs.append(BeamHingeDofType(d, "spring", float(bc.get("stiffness", "0"))))
        elif constraint in ("fixed", "free"):
            dofs.append(BeamHingeDofType(d, constraint))
        else:
            return None
    return BeamHinge(name, dofs)


def get_hinges(xml_root: ET.Element) -> dict[str, BeamHinge]:
    """``{name: BeamHinge}`` of every hinge the file defines that adapy can hold; each other one is reported."""
    rep = conversion_report.current()
    out: dict[str, BeamHinge] = {}
    for hinge in xml_root.iterfind(".//properties/hinges/hinge"):
        name = hinge.get("name")
        kind = next(iter(hinge), None)
        made = None
        if kind is not None and kind.get("coordinate_system_type", "local") != "local":
            why = f'a hinge in the {kind.get("coordinate_system_type")!r} frame; adapy holds hinges in the beam\'s own axes'
        elif kind is not None and kind.tag == "connectivity_hinge":
            made, why = _connectivity(name, kind), "a connectivity hinge without six 0/1 connectivities"
        elif kind is not None and kind.tag == "flexible_hinge":
            made, why = _flexible(name, kind), "a flexible hinge without one fixed/free/spring condition per dof"
        else:
            why = f"a hinge of kind {getattr(kind, 'tag', None)!r}"
        if made is None:
            rep.omitted(STAGE, "hinge", name, f"{why}: the beams naming it are read fully connected there")
            continue
        out[name] = made
    return out


def apply_end_hinges(bm_el: ET.Element, segs: list, hinges: dict[str, BeamHinge] | None) -> None:
    """The beam concept's ``end1`` / ``end2`` hinge on its first / last segment (a beam of several segments is one
    concept: its ends are the first segment's start and the last one's end)."""
    if not segs or not hinges:
        return
    for end, seg, attr in (("end1", segs[0], "hinge1"), ("end2", segs[-1], "hinge2")):
        el = bm_el.find(end)
        ref = None if el is None else el.get("hinge_ref")
        if ref and ref in hinges:
            setattr(seg, attr, hinges[ref])
