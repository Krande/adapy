import pytest

from ada import Assembly, Part, Pipe, Section
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
    a.to_ifc(test_dir / "pipe_bend.ifc")

    # a.to_stp(test_dir / "pipe_bend.stp")
    # dummy_display(a)


def test_write_elbow_revolved_solid(pipe_w_single_90_deg_bend):
    a = Assembly("MyTest") / (Part("MyPart") / pipe_w_single_90_deg_bend)
    elbow = pipe_w_single_90_deg_bend.segments[1]
    assert elbow.arc_seg.radius == pytest.approx(0.195958125)

    elbow_revolved_solid(elbow, a.ifc_file, a.ifc_file.by_type("IfcGeometricRepresentationContext")[0])

    # Settings.make_param_elbows = True
    # a.to_stp(test_dir / "pipe_bend.stp")
    # a.to_ifc(test_dir / "pipe_bend.ifc")
