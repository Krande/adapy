import pytest

from ada import Assembly, Part, Pipe, Section, PipeSegElbow
from ada.config import Settings
from ada.ifc.write.write_pipe import elbow_revolved_solid

test_dir = Settings.test_dir / "pipes"


def test_pipe_straight(dummy_display):
    a = Assembly("MyTest")

    p = Part("MyPart")
    a.add_part(p)
    z = 3.2
    y0 = -200e-3
    pipe1 = Pipe("Pipe1", [(0, y0, 0), (0, y0, z)], Section("PSec", "PIPE", r=0.10, wt=5e-3))
    p.add_pipe(pipe1)

    _ = a.to_ifc(test_dir / "pipe_straight.ifc", return_file_obj=True)
    dummy_display(a)


def test_pipe_multiple_bends(pipe_w_multiple_bends):

    assert pipe_w_multiple_bends.segments[1].bend_radius == pytest.approx(0.195958125)

    Settings.make_param_elbows = True
    a = Assembly("MyTest") / (Part("MyPart") / pipe_w_multiple_bends)
    a.to_stp(test_dir / "pipe_bend_multiple.stp")
    a.to_ifc(test_dir / "pipe_bend_multiple.ifc")
    # dummy_display(a)


def test_write_elbow_revolved_solid(pipe_w_single_90_deg_bend):
    a = Assembly("MyTest") / (Part("MyPart") / pipe_w_single_90_deg_bend)
    elbow = pipe_w_single_90_deg_bend.segments[1]
    assert elbow.arc_seg.radius == pytest.approx(0.195958125)

    elbow_revolved_solid(elbow, a.ifc_file, a.ifc_file.by_type("IfcGeometricRepresentationContext")[0])

    Settings.make_param_elbows = True
    # a.to_stp(test_dir / "pipe_bend.stp")
    a.to_ifc(test_dir / "pipe_bend.ifc")


def test_write_elbow_revolved_solid_ifc_gen(pipe_w_multiple_bends):
    a = Assembly("MyTest") / (Part("MyPart") / pipe_w_multiple_bends)

    elbows = list(filter(lambda x: isinstance(x, PipeSegElbow), pipe_w_multiple_bends.segments))
    context = a.ifc_file.by_type("IfcGeometricRepresentationContext")[0]

    elbow1 = elbows[0]

    # shape1 = elbow_revolved_solid(elbow1, a.ifc_file, context)
    # ifc_revolved_solid1 = shape1.Representations[0].Items[0]
    #
    # assert ifc_revolved_solid1.Angle == 90.0
    #
    # axis1 = ifc_revolved_solid1.Axis
    # assert axis1.Axis.DirectionRatios == pytest.approx((-1.0, 0.0, 0.0))
    # assert axis1.Location.Coordinates == pytest.approx((0.0, 0.195958125, 0.0))
    #
    # position1 = ifc_revolved_solid1.Position
    #
    # assert position1.Axis.DirectionRatios == pytest.approx((1.0, 0.0, 0.0))
    # assert position1.Location.Coordinates == pytest.approx((5.004041875, -0.2, 3.2))
    # assert position1.RefDirection.DirectionRatios == pytest.approx((0.0, 0.0, -1.0))

    elbow2 = elbows[1]

    shape2 = elbow_revolved_solid(elbow2, a.ifc_file, context)
    ifc_revolved_solid2 = shape2.Representations[0].Items[0]

    assert ifc_revolved_solid2.Angle == 90.0

    axis2 = ifc_revolved_solid2.Axis

    position2 = ifc_revolved_solid2.Position

    assert position2.Axis.DirectionRatios == pytest.approx((0.0, 1.0, 0.0))

    # assert position2.Location.Coordinates == pytest.approx((5.2, 4.604041875, 3.2))

    print('sd')

    elbow3 = elbows[2]

    shape3 = elbow_revolved_solid(elbow3, a.ifc_file, context)
    ifc_revolved_solid2 = shape2.Representations[0].Items[0]
