import ifcopenshell

import ada
from ada.cadit.ifc.read.geom.geom_reader import import_geometry_from_ifc_geom
from ada.cadit.ifc.read.geom.solids import (
    extruded_solid_area_tapered,
    fixed_reference_swept_area_solid,
)
from ada.cadit.ifc.write.geom.solids import (
    extruded_area_solid_tapered,
    fixed_reference_swept_area_solid as write_frs,
)
from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su


def test_extruded_area_solid_tapered_roundtrip():
    bm = ada.BeamTapered("b", (0, 0, 0), (0, 0, 1), "IPE400", "IPE300")
    geo = bm.solid_geom().geometry
    assert isinstance(geo, geo_so.ExtrudedAreaSolidTapered)

    f = ifcopenshell.file(schema="IFC4")
    ifc_tap = extruded_area_solid_tapered(geo, f)
    assert ifc_tap.is_a("IfcExtrudedAreaSolidTapered")

    back = extruded_solid_area_tapered(ifc_tap)
    assert isinstance(back, geo_so.ExtrudedAreaSolidTapered)
    assert isinstance(back.swept_area, geo_su.ArbitraryProfileDef)
    assert isinstance(back.end_swept_area, geo_su.ArbitraryProfileDef)
    assert back.depth == geo.depth

    # The top-level dispatch must route Tapered to the tapered reader, not the base reader.
    routed = import_geometry_from_ifc_geom(ifc_tap)
    assert isinstance(routed, geo_so.ExtrudedAreaSolidTapered)


def test_fixed_reference_swept_area_solid_roundtrip():
    sweep = ada.PrimSweep("s", [(0, 0, 0), (0.5, 0, 0, 0.2), (0.8, 0.8, 1)], [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)])
    geo = sweep.solid_geom().geometry
    assert isinstance(geo, geo_so.FixedReferenceSweptAreaSolid)

    f = ifcopenshell.file(schema="IFC4")
    ifc_frs = write_frs(geo, f)
    assert ifc_frs.is_a("IfcFixedReferenceSweptAreaSolid")
    assert ifc_frs.FixedReference is not None

    back = fixed_reference_swept_area_solid(ifc_frs)
    assert isinstance(back, geo_so.FixedReferenceSweptAreaSolid)
    assert isinstance(back.swept_area, geo_su.ArbitraryProfileDef)
    assert isinstance(back.directrix, geo_cu.IndexedPolyCurve)

    routed = import_geometry_from_ifc_geom(ifc_frs)
    assert isinstance(routed, geo_so.FixedReferenceSweptAreaSolid)
