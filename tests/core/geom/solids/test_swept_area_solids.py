import numpy as np

import ada
from ada.geom import solids as geo_so


def test_sweep_shape(tmp_path):
    sweep_curve = [(0, 0, 0), (5, 5.0, 0.0, 1), (10, 0, 0)]
    ot = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
    shape = ada.PrimSweep("MyShape", sweep_curve, ot)

    a = ada.Assembly("SweptShapes", units="m") / [ada.Part("MyPart") / [shape]]
    a.to_ifc(tmp_path / "swept_shape.ifc", file_obj_only=True, validate=True)


def test_prim_sweep1(tmp_path):
    curve3d = [
        (0, 0, 0),
        (0.5, 0.5, 0.5, 0.2),
        (0.5, 1, 0.5),
        (1, 1, 0.5),
    ]
    h = 0.01
    profile2d = [(0, 0), (h, 0), (h, h), (0, h)]
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, color="red")
    geom = sweep.solid_geom()

    assert isinstance(geom.geometry, geo_so.FixedReferenceSweptAreaSolid)

    sweep.solid_occ()

    a = ada.Assembly("SweptShapes") / [ada.Part("MyPart") / [sweep]]
    a.to_ifc(tmp_path / "my_swept_shape_m.ifc", file_obj_only=True, validate=True)


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
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, profile_normal=(0, -1, 0), profile_ydir=(0, 0, 1), color="red")
    sweep_solid = sweep.solid_geom()
    sweep_geom = sweep_solid.geometry
    assert isinstance(sweep_geom, geo_so.FixedReferenceSweptAreaSolid)

    # Directrix
    sweep_geom_directrix = sweep_geom.directrix
    assert sweep_geom_directrix.start.is_equal(ada.Point(0, 0, 0))
    assert sweep_geom_directrix.end.is_equal(ada.Point(0, 1, 0))

    # Position
    sweep_geom_position = sweep_geom.position
    assert sweep_geom_position.axis.is_equal(ada.Direction(0, 0, 1))
    assert sweep_geom_position.location.is_equal(ada.Point(0, 0, 0))
    assert sweep_geom_position.ref_direction.is_equal(ada.Direction(1, 0, 0))

    sweep_segments = sweep_geom.swept_area.outer_curve.segments
    assert len(sweep_segments) == 3
    assert sweep_segments[0].start.is_equal(ada.Point(1, 0, 1))
    assert sweep_segments[0].end.is_equal(ada.Point(0, 0, 0))
    assert sweep_segments[1].start.is_equal(ada.Point(0, 0, 0))
    assert sweep_segments[1].end.is_equal(ada.Point(1, 0, 0))
    assert sweep_segments[2].start.is_equal(ada.Point(1, 0, 0))
    assert sweep_segments[2].end.is_equal(ada.Point(1, 0, 1))

    sweep_flipped = ada.PrimSweep(
        "sweep1_flipped", curve3d, profile2d, profile_normal=(0, 1, 0), profile_ydir=(0, 0, 1), color="blue"
    )
    fsweep_solid = sweep_flipped.solid_geom()
    fsweep_geom = fsweep_solid.geometry
    assert isinstance(fsweep_geom, geo_so.FixedReferenceSweptAreaSolid)

    # Directrix should be the same as the original sweep
    fsweep_geom_directrix = fsweep_geom.directrix
    assert fsweep_geom_directrix.start.is_equal(ada.Point(0, 0, 0))
    assert fsweep_geom_directrix.end.is_equal(ada.Point(0, 1, 0))

    # Position should be the same as the original sweep
    fsweep_geom_position = fsweep_geom.position
    assert fsweep_geom_position.axis.is_equal(ada.Direction(0, 0, 1))
    assert fsweep_geom_position.location.is_equal(ada.Point(0, 0, 0))
    assert fsweep_geom_position.ref_direction.is_equal(ada.Direction(1, 0, 0))

    fsweep_segments = fsweep_geom.swept_area.outer_curve.segments
    assert len(fsweep_segments) == 3
    assert fsweep_segments[0].start.is_equal(ada.Point(-1, 0, 1))
    assert fsweep_segments[0].end.is_equal(ada.Point(0, 0, 0))
    assert fsweep_segments[1].start.is_equal(ada.Point(0, 0, 0))
    assert fsweep_segments[1].end.is_equal(ada.Point(-1, 0, 0))
    assert fsweep_segments[2].start.is_equal(ada.Point(-1, 0, 0))
    assert fsweep_segments[2].end.is_equal(ada.Point(-1, 0, 1))

    # p = ada.Part("MyPart") / [sweep, sweep_flipped]
    # p.show()


def test_swept_angled_flipped():
    wt = 8e-3
    fillet = [(0, 0), (-wt, 0), (0, wt)]
    profile_y = (0, 0, 1)

    sweep1_pts = [[287.85, 99.917, 513.26], [287.85, 100.083, 513.26]]
    sweep1_profile_normal = [0.0, 1.0, 0.0]

    sweep2_pts = [[287.833, 100.1, 513.26], [287.667, 100.1, 513.26]]
    sweep2_profile_normal = [-1.0, -0.0, -0.0]

    sweep3_pts = [[287.833, 99.9, 513.26], [287.667, 99.9, 513.26]]
    sweep3_profile_normal = [1.0, -0.0, -0.0]

    sweep1 = ada.PrimSweep(
        "sweep1", sweep1_pts, fillet, profile_normal=sweep1_profile_normal, profile_ydir=profile_y, color="red"
    )
    sweep2 = ada.PrimSweep(
        "sweep2", sweep2_pts, fillet, profile_normal=sweep2_profile_normal, profile_ydir=profile_y, color="blue"
    )
    sweep3 = ada.PrimSweep(
        "sweep3", sweep3_pts, fillet, profile_normal=sweep3_profile_normal, profile_ydir=profile_y, color="green"
    )
    sweeps = [sweep1, sweep2, sweep3]
    assert len(sweeps) == 3

    # p = ada.Part("part") / sweeps
    # p.show(stream_from_ifc_store=False)
    # (ada.Assembly("SweptShapes") / p).to_ifc("swept_shape.ifc", validate=True)


def test_swept_angled_flipped_many_pts():
    from ada.param_models.sweep_example import (
        get_three_sweeps_mesh_data,
        sweep1_pts,
        sweep2_pts,
        sweep3_pts,
    )

    wt = 8e-3
    fillet = [(0, 0), (-wt, 0), (0, wt)]

    profile_y = ada.Direction(0, 0, 1)
    sweep1_profile_normal = [0.0, 1.0, 0.0]
    sweep2_profile_normal = [-1.0, 0.0, 0.0]
    sweep3_profile_normal = [1.0, 0.0, 0.0]
    sweep3_profile_xdir = [0, 1, 0]

    sweep1 = ada.PrimSweep(
        "sweep1", sweep1_pts, fillet, profile_normal=sweep1_profile_normal, profile_ydir=profile_y, color="red"
    )
    sweep2 = ada.PrimSweep(
        "sweep2", sweep2_pts, fillet, profile_normal=sweep2_profile_normal, profile_ydir=profile_y, color="blue"
    )
    sweep3 = ada.PrimSweep(
        "sweep3",
        sweep3_pts,
        fillet,
        profile_normal=sweep3_profile_normal,
        profile_xdir=sweep3_profile_xdir,
        color="green",
    )
    sweeps = [sweep1, sweep2, sweep3]
    mesh_data = get_three_sweeps_mesh_data()
    mesh1_raw = mesh_data[0]
    mesh1 = sweeps[0].solid_trimesh()
    mesh1_raw_vertices = mesh1_raw["vertices"]
    mesh1_vertices = mesh1.vertices.tolist()
    assert len(mesh1_raw_vertices) == len(mesh1_vertices)

    # a = ada.Assembly("part") / sweeps
    # a.show(stream_from_ifc_store=True)
    # a.show(stream_from_ifc_store=False)
    # a.to_ifc("swept_shape.ifc", validate=True)
