import os

from ada.cadit.sat.store import SatReaderFactory
from ada.cadit.ifc.store import IfcStore


def test_read_a_curved_plate():
    sat_reader = SatReaderFactory('curved_plate.sat')
    bsplines = list(sat_reader.iter_bspline_objects())
    assert len(bsplines) == 1

    bspline = bsplines[0]
    ifc_store = IfcStore()
    ifc = ifc_store.f
    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcgeometryresource/lexical/ifcrationalbsplinesurfacewithknots.htm
    # ifc.add(ifc.create_entity('IfcRationalBSplineSurfaceWithKnots', ...))
    os.makedirs('temp', exist_ok=True)
    ifc_store.save_to_file('temp/curved_plate.ifc')