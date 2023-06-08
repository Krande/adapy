import pytest

from ada import Assembly, Part, Pipe, PipeSegElbow, Section
from ada.cadit.ifc.write.write_pipe import elbow_revolved_solid
from ada.config import Settings

test_dir = Settings.test_dir / "pipes"


def test_pipe_straight():
    a = Assembly("MyTest")
    p = a.add_part(Part("MyPart"))
    y0 = -200e-3
    p.add_pipe(Pipe("Pipe1", [(0, y0, 0), (0, y0, 3.2)], Section("PSec", "PIPE", r=0.10, wt=5e-3)))
    _ = a.to_ifc(test_dir / "pipe_straight.ifc", file_obj_only=True, validate=True)


def test_write_single_90_deg_elbow_revolved_solid(pipe_w_single_90_deg_bend):
    a = Assembly("MyTest") / (Part("MyPart") / pipe_w_single_90_deg_bend)
    elbow = pipe_w_single_90_deg_bend.segments[1]
    assert elbow.arc_seg.radius == pytest.approx(0.1979375)

    f = a.ifc_store.f
    a.ifc_store.sync()

    elbow_revolved_solid(elbow, f)

    # a.to_stp(test_dir / "pipe_bend.stp")
    # a.to_ifc(test_dir / "pipe_bend.ifc")


def test_pipe_multiple_bends(pipe_w_multiple_bends):
    assert pipe_w_multiple_bends.segments[1].bend_radius == pytest.approx(0.1979375)

    a = Assembly("MyTest") / (Part("MyPart") / pipe_w_multiple_bends)
    # a.to_stp(test_dir / "pipe_bend_multiple.stp")
    a.to_ifc(test_dir / "pipe_bend_multiple.ifc", file_obj_only=True, validate=True)


def test_write_elbow_revolved_solid_ifc_gen(pipe_w_multiple_bends):
    a = Assembly("MyTest", schema="IFC4x3") / (Part("MyPart") / pipe_w_multiple_bends)
    a.ifc_store.sync()
    # a.to_ifc('temp/pipes.ifc', file_obj_only=True, validate=True)
    f = a.ifc_store.f

    elbows = list(filter(lambda x: isinstance(x, PipeSegElbow), pipe_w_multiple_bends.segments))

    elbow1 = elbows[0]

    shape1 = elbow_revolved_solid(elbow1, f)
    ifc_revolved_solid1 = shape1.Representations[0].Items[0]

    assert ifc_revolved_solid1.Angle == 90.0

    axis1 = ifc_revolved_solid1.Axis
    assert axis1.Axis.DirectionRatios == pytest.approx((1.0, 0.0, 0.0))
    assert axis1.Location.Coordinates == pytest.approx((0.0, -0.1979375, 0.0), abs=1e-6)

    position1 = ifc_revolved_solid1.Position

    assert position1.Axis.DirectionRatios == pytest.approx((1.0, 0.0, 0.0))
    assert position1.Location.Coordinates == pytest.approx((5.0020625, -0.2, 3.2))
    assert position1.RefDirection.DirectionRatios == pytest.approx((0.0, 0.0, 1.0))

    elbow2 = elbows[1]

    shape2 = elbow_revolved_solid(elbow2, f)
    ifc_revolved_solid2 = shape2.Representations[0].Items[0]

    assert ifc_revolved_solid2.Angle == pytest.approx(90.0)

    position2 = ifc_revolved_solid2.Position

    assert position2.Axis.DirectionRatios == pytest.approx((0.0, 1.0, 0.0))

    elbow3 = elbows[2]

    shape3 = elbow_revolved_solid(elbow3, f)
    ifc_revolved_solid3 = shape3.Representations[0].Items[0]

    assert ifc_revolved_solid3.Angle == pytest.approx(67.380135)
