import ada


def test_roundtrip_pipe_straight(tmp_path):
    # A custom circular-hollow section round-trips exactly via the ADA parameter bag on the
    # segment's IfcCircleHollowProfileDef.
    pipe = ada.Pipe("Pipe1", [(0, 0, 0), (0, 0, 3.2)], ada.Section("PSec", "PIPE", r=0.10, wt=5e-3))
    fp = (ada.Assembly() / (ada.Part("MyPart") / pipe)).to_ifc(tmp_path / "pipe.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    p: ada.Pipe = a.get_by_name("Pipe1")

    assert isinstance(p, ada.Pipe)
    assert p.parent.name == "MyPart"
    assert len(p.segments) == 1
    assert isinstance(p.segments[0], ada.PipeSegStraight)
    # parametric section preserved (not just the catalog name)
    sec = p.segments[0].section
    assert sec.type == ada.Section.TYPES.TUBULAR
    assert sec.r == 0.10
    assert sec.wt == 5e-3


def test_roundtrip_pipe_with_bends(tmp_path):
    points = [(0, 0, 0), (5, 0, 0), (5, 5, 0), (5, 5, 3)]
    pipe = ada.Pipe("BentPipe", points, ada.Section("PSec", "PIPE", r=0.10, wt=5e-3))
    n_straight = sum(isinstance(s, ada.PipeSegStraight) for s in pipe.segments)
    n_elbow = sum(isinstance(s, ada.PipeSegElbow) for s in pipe.segments)

    fp = (ada.Assembly() / (ada.Part("MyPart") / pipe)).to_ifc(tmp_path / "bent.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    p: ada.Pipe = a.get_by_name("BentPipe")

    assert isinstance(p, ada.Pipe)
    # segment composition (straights + elbows) preserved
    assert sum(isinstance(s, ada.PipeSegStraight) for s in p.segments) == n_straight
    assert sum(isinstance(s, ada.PipeSegElbow) for s in p.segments) == n_elbow
    # every segment (incl. elbows) round-trips its parametric section via the ADA bag
    assert n_elbow > 0
    for s in p.segments:
        assert s.section.type == ada.Section.TYPES.TUBULAR
        assert s.section.r == 0.10
        assert s.section.wt == 5e-3
