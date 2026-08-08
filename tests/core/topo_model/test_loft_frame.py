"""A structural (non-SURFACE_ONLY) loft member is framed by the SteelStru
blueprint over its loft-derived cell graph — the same beams boxes get — while a
SURFACE_ONLY member (or blueprint_name='none') stays a plate skin.
"""

from __future__ import annotations

import ada
from ada.topo_model import ProceduralBuilder
from ada.topology.entities import LoftStation, TopoLoftMember


def _st(z: float, w: float) -> LoftStation:
    return LoftStation(TYPE="rectangle", X=0, Y=0, Z=z, WIDTH=w, HEIGHT=w)


def _jacket() -> TopoLoftMember:
    # Tapered 4-station rectangular stack (a jacket-like loft).
    return TopoLoftMember(NAME="jacket", STATIONS=[_st(0, 50), _st(46, 50), _st(120, 38), _st(200, 25)])


def test_structural_loft_member_is_framed_with_beams():
    pb = ProceduralBuilder(spaces=[], loft_members=[_jacket()])  # steel_stru default
    pb.build_structure()
    pb.build_lofts()

    lofts = pb.assembly.parts["Lofts"]
    beams = list(lofts.get_all_physical_objects(by_type=ada.Beam))
    plates = list(lofts.get_all_physical_objects(by_type=ada.Plate))
    sections = {b.section.name for b in beams}

    # SteelStru frames the loft cells: HEB200 legs, IPE200 ring girders, HP140x8
    # stringers, and floor plates at each station level.
    assert beams, "structural loft member should emit blueprint beams"
    assert {"HEB200", "IPE200", "HP140x8"} <= sections, f"missing frame sections: {sections}"
    assert plates, "framed loft should carry floor plates"
    # The band-cell topology is still built (for selection/picking).
    assert pb.loft_cell_graph is not None


def test_surface_only_loft_member_stays_a_plate_skin():
    m = _jacket()
    m.SURFACE_ONLY = True
    pb = ProceduralBuilder(spaces=[], loft_members=[m])
    pb.build_structure()
    pb.build_lofts()

    lofts = pb.assembly.parts["Lofts"]
    assert not list(lofts.get_all_physical_objects(by_type=ada.Beam)), "skin member must not emit beams"
    assert list(lofts.get_all_physical_objects(by_type=ada.Plate)), "skin member should be plates"


def test_jacket_representation_is_open_tubular_truss():
    m = _jacket()
    m.REPRESENTATION = "JACKET"
    pb = ProceduralBuilder(spaces=[], loft_members=[m])
    pb.build_structure()
    pb.build_lofts()

    lofts = pb.assembly.parts["Lofts"]
    beams = list(lofts.get_all_physical_objects(by_type=ada.Beam))
    plates = list(lofts.get_all_physical_objects(by_type=ada.Plate))

    # Open truss: tubular legs + ring + diagonal braces, no decks/stringers/plates.
    assert beams, "jacket should emit truss beams"
    assert all(str(b.section.type) == "BaseTypes.TUBULAR" for b in beams), "jacket members must be tubular"
    assert not plates, "an open jacket truss carries no plates"
    names = {b.name.split("_")[0] for b in beams}
    assert {"Leg", "Ring", "Brace"} <= names, f"missing truss members: {names}"
    # And it compiles to a GLB.
    glb = pb.compile()
    assert glb[:4] == b"glTF"


def test_blueprint_none_keeps_loft_as_skin():
    pb = ProceduralBuilder(spaces=[], loft_members=[_jacket()], blueprint_name="none")
    pb.build_structure()
    pb.build_lofts()

    lofts = pb.assembly.parts["Lofts"]
    assert not list(lofts.get_all_physical_objects(by_type=ada.Beam)), "blueprint 'none' emits no beams"
    assert list(lofts.get_all_physical_objects(by_type=ada.Plate))
