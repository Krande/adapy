import numpy as np

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


def test_prim_sweep2(tmp_path):
    sweep = ada.PrimSweep(
        "sweep1",
        [(0, 0, 0), (0.5, 0, 0, 0.2), (0.8, 0.8, 1), (0.8, 0.8, 2)],
        [(0, 0), (0.1, 0), (0.1, 0.1), (0, 0.1)],
        (0, 0, 1),
        (1, 0, 0),
        origin=(0, 0, 0),
    )
    geom = sweep.solid_geom()
    assert isinstance(geom.geometry, geo_so.FixedReferenceSweptAreaSolid)
    mesh = sweep.solid_trimesh()

    # sweep.show()

    expected = np.asarray([0.622505, 0.415516, 0.808411])
    np.testing.assert_allclose(mesh.center_mass, expected, atol=0.0001)



def test_prim_sweep_flipped_normals(tmp_path):
    curve3d = [
        (0, 0, 0),
        (0, 1, 0),
    ]
    profile2d = [(0, 0), (1, 0), (1, 1)]
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, profile_normal=(0,-1,0), profile_ydir=(0,0,1), color="red")
    sweep_solid = sweep.solid_geom()
    sweep_geom = sweep_solid.geometry
    assert isinstance(sweep_geom, geo_so.FixedReferenceSweptAreaSolid)

    # Directrix
    sweep_geom_directrix = sweep_geom.directrix
    assert sweep_geom_directrix.start.is_equal(ada.Point(0,0,0))
    assert sweep_geom_directrix.end.is_equal(ada.Point(0,0,-1))

    # Position
    sweep_geom_position = sweep_geom.position
    assert sweep_geom_position.axis.is_equal(ada.Direction(0,-1,0))
    assert sweep_geom_position.location.is_equal(ada.Point(0,0,0))
    assert sweep_geom_position.ref_direction.is_equal(ada.Direction(1,0,0))

    sweep_flipped = ada.PrimSweep("sweep1_flipped", curve3d, profile2d, profile_normal=(0,1,0), profile_ydir=(0,0,1), color="blue")
    fsweep_solid = sweep_flipped.solid_geom()
    fsweep_geom = fsweep_solid.geometry
    assert isinstance(fsweep_geom, geo_so.FixedReferenceSweptAreaSolid)

    # p = ada.Part("MyPart") / [sweep, sweep_flipped]
    # p.show()

    # Directrix should be the same as the original sweep
    fsweep_geom_directrix = fsweep_geom.directrix
    assert fsweep_geom_directrix.start.is_equal(ada.Point(0,0,0))
    assert fsweep_geom_directrix.end.is_equal(ada.Point(0,0,1))

    # Position should be the same as the original sweep
    fsweep_geom_position = fsweep_geom.position
    assert fsweep_geom_position.axis.is_equal(ada.Direction(0,1,0))
    assert fsweep_geom_position.location.is_equal(ada.Point(0,0,0))
    assert fsweep_geom_position.ref_direction.is_equal(ada.Direction(-1,0,0))

