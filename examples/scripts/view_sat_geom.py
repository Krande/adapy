import pathlib

import ada
from ada.cadit.sat.store import SatReaderFactory

THIS_DIR = pathlib.Path(__file__).parent
ROOT_DIR = THIS_DIR.parent.parent


def run_this():
    sat_reader = SatReaderFactory(ROOT_DIR / "files/sat_files/3_plates_ellipse.sat")
    shapes = []
    for acis_record, face in sat_reader.iter_all_faces():
        shp1 = ada.Shape("plate", ada.geom.Geometry(1, face, None))
        shapes.append(shp1)

    a = ada.Assembly() / shapes
    a.ifc_store.sync()
    a.show(stream_from_ifc=True)


if __name__ == '__main__':
    run_this()
