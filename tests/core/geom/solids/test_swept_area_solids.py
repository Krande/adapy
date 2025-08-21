import numpy as np

import ada
from ada.geom import solids as geo_so


def test_sweep_shape(tmp_path):
    sweep_curve = [(0, 0, 0), (5, 5.0, 0.0, 1), (10, 0, 0)]
    ot = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
    shape = ada.PrimSweep("MyShape", sweep_curve, ot)

    a = ada.Assembly("SweptShapes", units="m") / [ada.Part("MyPart") / [shape]]
    a.to_ifc(tmp_path / "swept_shape.ifc", validate=True)


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
    sweep = ada.PrimSweep("sweep1", curve3d, profile2d, profile_normal=(0, -1, 0), profile_ydir=(0, 0, 1), color="red")
    sweep_solid = sweep.solid_geom()
    sweep_geom = sweep_solid.geometry
    assert isinstance(sweep_geom, geo_so.FixedReferenceSweptAreaSolid)

    # Directrix
    sweep_geom_directrix = sweep_geom.directrix
    assert sweep_geom_directrix.start.is_equal(ada.Point(0, 0, 0))
    assert sweep_geom_directrix.end.is_equal(ada.Point(0, 0, -1))

    # Position
    sweep_geom_position = sweep_geom.position
    assert sweep_geom_position.axis.is_equal(ada.Direction(0, -1, 0))
    assert sweep_geom_position.location.is_equal(ada.Point(0, 0, 0))
    assert sweep_geom_position.ref_direction.is_equal(ada.Direction(1, 0, 0))

    sweep_flipped = ada.PrimSweep(
        "sweep1_flipped", curve3d, profile2d, profile_normal=(0, 1, 0), profile_ydir=(0, 0, 1), color="blue"
    )
    fsweep_solid = sweep_flipped.solid_geom()
    fsweep_geom = fsweep_solid.geometry
    assert isinstance(fsweep_geom, geo_so.FixedReferenceSweptAreaSolid)

    # p = ada.Part("MyPart") / [sweep, sweep_flipped]
    # p.show()

    # Directrix should be the same as the original sweep
    fsweep_geom_directrix = fsweep_geom.directrix
    assert fsweep_geom_directrix.start.is_equal(ada.Point(0, 0, 0))
    assert fsweep_geom_directrix.end.is_equal(ada.Point(0, 0, 1))

    # Position should be the same as the original sweep
    fsweep_geom_position = fsweep_geom.position
    assert fsweep_geom_position.axis.is_equal(ada.Direction(0, 1, 0))
    assert fsweep_geom_position.location.is_equal(ada.Point(0, 0, 0))
    assert fsweep_geom_position.ref_direction.is_equal(ada.Direction(-1, 0, 0))


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
    sweep1_profile_xdir = sweep1.profile_curve_outer.xdir
    sweep2_profile_xdir = sweep2.profile_curve_outer.xdir
    sweep3_profile_xdir = sweep3.profile_curve_outer.xdir

    print(sweep1_profile_xdir)
    print(sweep2_profile_xdir)
    print(sweep3_profile_xdir)

    p = ada.Part("part") / [sweep1, sweep2, sweep3]
    p.show(stream_from_ifc_store=False)


def test_swept_angled_flipped_many_pts():
    wt = 8e-3
    fillet = [(0, 0), (-wt, 0), (0, wt)]


    sweep1_pts = [
        [287.85, 99.917, 513.26],
        [287.85, 100.083, 513.26],
        [287.85, 100.08950561835023, 513.2587059520527],
        [287.85, 100.09502081528021, 513.2550208152801],
        [287.85, 100.09870595205274, 513.2495056183502],
        [287.85, 100.10000000000005, 513.2429999999999],
        [287.85, 100.1, 513.077],
        [287.85, 100.09870595205268, 513.0704943816498],
        [287.85, 100.09502081528017, 513.0649791847198],
        [287.85, 100.0895056183502, 513.0612940479473],
        [287.85, 100.083, 513.06],
        [287.85, 99.917, 513.06],
        [287.85, 99.91049438164977, 513.0612940479473],
        [287.85, 99.90497918471979, 513.0649791847198],
        [287.85, 99.90129404794726, 513.0704943816497],
        [287.85, 99.89999999999995, 513.077],
        [287.85, 99.9, 513.2429999999999],
        [287.85, 99.90129404794732, 513.2495056183501],
        [287.85, 99.90497918471983, 513.2550208152801],
        [287.85, 99.9104943816498, 513.2587059520527],
        [287.85, 99.917, 513.26],
    ]

    sweep2_pts = [
        [287.833, 100.1, 513.26],
        [287.667, 100.1, 513.26],
        [287.66049438164976, 100.1, 513.2587059520527],
        [287.65497918471976, 100.1, 513.2550208152801],
        [287.65129404794726, 100.1, 513.2495056183502],
        [287.6499999999999, 100.1, 513.2429999999999],
        [287.65, 100.1, 513.077],
        [287.65129404794726, 100.1, 513.0704943816498],
        [287.6549791847198, 100.1, 513.0649791847198],
        [287.66049438164976, 100.1, 513.0612940479473],
        [287.667, 100.1, 513.06],
        [287.833, 100.1, 513.06],
        [287.83950561835024, 100.1, 513.0612940479473],
        [287.84502081528024, 100.1, 513.0649791847198],
        [287.84870595205274, 100.1, 513.0704943816497],
        [287.8500000000001, 100.1, 513.077],
        [287.85, 100.1, 513.2429999999999],
        [287.84870595205274, 100.1, 513.2495056183501],
        [287.8450208152802, 100.1, 513.2550208152801],
        [287.83950561835024, 100.1, 513.2587059520527],
        [287.833, 100.1, 513.26],
    ]

    sweep3_pts = [
        [287.833, 99.9, 513.26],
        [287.667, 99.9, 513.26],
        [287.66049438164976, 99.9, 513.2587059520527],
        [287.65497918471976, 99.9, 513.2550208152801],
        [287.65129404794726, 99.9, 513.2495056183502],
        [287.6499999999999, 99.9, 513.2429999999999],
        [287.65, 99.9, 513.077],
        [287.65129404794726, 99.9, 513.0704943816498],
        [287.6549791847198, 99.9, 513.0649791847198],
        [287.66049438164976, 99.9, 513.0612940479473],
        [287.667, 99.9, 513.06],
        [287.833, 99.9, 513.06],
        [287.83950561835024, 99.9, 513.0612940479473],
        [287.84502081528024, 99.9, 513.0649791847198],
        [287.84870595205274, 99.9, 513.0704943816497],
        [287.8500000000001, 99.9, 513.077],
        [287.85, 99.9, 513.2429999999999],
        [287.84870595205274, 99.9, 513.2495056183501],
        [287.8450208152802, 99.9, 513.2550208152801],
        [287.83950561835024, 99.9, 513.2587059520527],
        [287.833, 99.9, 513.26],
    ]

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
    sweep1_profile_xdir = sweep1.profile_curve_outer.xdir
    sweep2_profile_xdir = sweep2.profile_curve_outer.xdir
    sweep3_profile_xdir = sweep3.profile_curve_outer.xdir

    print(sweep1_profile_xdir)
    print(sweep2_profile_xdir)
    print(sweep3_profile_xdir)

    p = ada.Part("part") / [sweep1, sweep2, sweep3]
    p.show(stream_from_ifc_store=False)
