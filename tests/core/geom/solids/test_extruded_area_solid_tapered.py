import ada
from ada.cad import active_backend
from ada.geom import solids as geo_so


def test_ipe_beam_taper():
    bm = ada.BeamTapered("my_beam_z", (0, 0, 0), (0, 0, 1), "IPE400", "IPE300")
    _ = ada.Part("my_part") / bm
    assert bm.section.h == 0.4
    assert bm.taper.h == 0.3

    geo = bm.solid_geom()
    assert isinstance(geo.geometry, geo_so.ExtrudedAreaSolidTapered)
    assert geo.geometry.depth == 1.0

    occ_shape = active_backend().build(geo)
    assert active_backend().shape_type(occ_shape) == "solid"
