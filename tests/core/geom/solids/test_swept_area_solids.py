import ada
from ada.geom import solids as geo_so


def test_prim_sweep1(tmp_path):
    curve3d = [
        (0, 0, 0),
        (0.5, 0.5, 0.5, 0.2),
        (0.5, 1, 0.5),
        (1, 1, 0.5),
    ]
    profile2d = [(0, 0), (1, 0), (1, 1), (0, 1)]
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, color="red")
    geom = sweep.solid_geom()

    assert isinstance(geom.geometry, geo_so.FixedReferenceSweptAreaSolid)

    sweep.solid_occ()

    a = ada.Assembly("SweptShapes") / [ada.Part("MyPart") / [sweep]]
    a.to_ifc(tmp_path / "my_swept_shape_m.ifc", file_obj_only=False, validate=True)
