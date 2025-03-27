import numpy as np
import pytest

import ada
from ada.core.vector_utils import angle_between


def test_placement_beam():
    bm = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "IPE300")

    bm2 = bm.copy_to("bm2", (0, 0, 1))
    bm2.placement = bm2.placement.rotate((0, 0, 1), 45)
    so_geo = bm2.solid_geom()
    assert so_geo.geometry.position.axis.is_equal(ada.Direction([0.70710678, 0.70710678, 0.0]))
    assert so_geo.geometry.position.location.is_equal(ada.Point([0.0, 0.0, 1.0]))


def test_place_copied_part():
    pl = ada.Plate("pl1", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01)
    beams = ada.Beam.array_from_list_of_coords(pl.poly.points3d, "IPE300", make_closed=True)
    p = ada.Part("myPart") / (pl, *beams)

    move_dir = ada.Direction(0, 0, 1)
    new_pos = ada.Point(0, 0, 1)

    copied_p = p.copy_to("my_copied_part", move_dir)
    pl1_copy: ada.Plate = copied_p.get_by_name("pl1_copy")
    assert isinstance(pl1_copy, ada.Plate)
    bm1_copy: ada.Beam = copied_p.get_by_name("bm1_copy")
    assert isinstance(bm1_copy, ada.Beam)
    pl_copy_place = pl1_copy.placement.get_absolute_placement()
    bm_copy_place = bm1_copy.placement.get_absolute_placement()

    pl_copy_so_geo = pl1_copy.solid_geom()
    bm_copy_so_geo = bm1_copy.solid_geom()

    assert ada.Direction(pl_copy_place.origin - pl.placement.origin).is_equal(move_dir)
    assert ada.Direction(bm_copy_place.origin - bm1_copy.placement.origin).is_equal(move_dir)

    assert pl_copy_so_geo.geometry.position.location.is_equal(new_pos)
    assert bm_copy_so_geo.geometry.position.location.is_equal(new_pos)


def test_place_copied_part_w_rotation():
    pl = ada.Plate("pl1", [(0, 0), (5, 0), (5, 5), (0, 5)], 0.01)
    beams = ada.Beam.array_from_list_of_coords(pl.poly.points3d, "IPE300", make_closed=True)
    p = ada.Part("myPart") / (pl, *beams)

    copied_p = p.copy_to("my_copied_part", (0, 0, 1), rotation_axis=(0, 0, 1), rotation_angle=45)

    pl1_copy: ada.Plate = copied_p.get_by_name("pl1_copy")
    bm1_copy: ada.Beam = copied_p.get_by_name("bm1_copy")

    pl_copy_place = pl1_copy.placement.get_absolute_placement(True)
    bm_copy_place = bm1_copy.placement.get_absolute_placement(True)

    pl_copy_so_geo = pl1_copy.solid_geom()
    bm_copy_so_geo = bm1_copy.solid_geom()

    move_dir = ada.Direction(0, 0, 1)
    new_pos = ada.Point(0, 0, 1)

    assert ada.Direction(pl_copy_place.origin - pl.placement.origin).is_equal(move_dir)
    assert ada.Direction(bm_copy_place.origin - bm1_copy.placement.origin).is_equal(move_dir)

    assert pl_copy_so_geo.geometry.position.location.is_equal(new_pos)
    assert bm_copy_so_geo.geometry.position.location.is_equal(new_pos)

    assert angle_between(pl.placement.xdir, pl_copy_place.xdir) == pytest.approx(np.deg2rad(45))
