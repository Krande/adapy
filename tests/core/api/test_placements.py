import ada


def test_placement_beam():
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")

    bm2 = bm.copy_to("bm2", (0, 0, 1))
    bm2.placement = bm2.placement.rotate((0, 0, 1), 45)
    so_geo = bm2.solid_geom()
    assert so_geo.geometry.position.axis.is_equal(ada.Direction([0.70710678, -0.70710678, 0.0]))
    assert so_geo.geometry.position.location.is_equal(ada.Point([0.0, 0.0, 1.0]))


def test_place_copied_part():
    pl = ada.Plate("pl1", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01)
    beams = ada.Beam.array_from_list_of_coords(pl.poly.points3d, "IPE300", make_closed=True)
    p = ada.Part("myPart") / (pl, *beams)

    copied_p = p.copy_to("my_copied_part", (0, 0, 1))
    pl1_copy = copied_p.get_by_name("pl1_copy")
    bm1_copy = copied_p.get_by_name("bm1_copy")

    pl_copy_so_geo = pl1_copy.solid_geom()
    bm_copy_so_geo = bm1_copy.solid_geom()
    assert len()


def test_place_copied_part_w_rotation():
    pl = ada.Plate("pl1", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01)
    beams = ada.Beam.array_from_list_of_coords(pl.poly.points3d, "IPE300", make_closed=True)
    p = ada.Part("myPart") / (pl, *beams)

    copied_p = p.copy_to("my_copied_part", (0, 0, 1))
    copied_p.placement = copied_p.placement.rotate((0, 0, 1), 45)

    pl1_copy = copied_p.get_by_name("pl1_copy")
    bm1_copy = copied_p.get_by_name("bm1_copy")

    pl_copy_so_geo = pl1_copy.solid_geom()
    bm_copy_so_geo = bm1_copy.solid_geom()
    assert len()
