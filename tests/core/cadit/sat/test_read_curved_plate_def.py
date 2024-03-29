import os

from ada.cadit.ifc.store import IfcStore
from ada.cadit.sat.store import SatReaderFactory


def test_read_a_curved_plate(example_files):
    sat_reader = SatReaderFactory(example_files / "sat_files/curved_plate.sat")
    bsplines = list(sat_reader.iter_bspline_objects())
    assert len(bsplines) == 1

    bsplines[0]
    ifc_store = IfcStore()
    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcgeometryresource/lexical/ifcrationalbsplinesurfacewithknots.htm
    # ifc.add(ifc.create_entity('IfcRationalBSplineSurfaceWithKnots', ...))
    os.makedirs("temp", exist_ok=True)
    ifc_store.save_to_file("temp/curved_plate.ifc")
