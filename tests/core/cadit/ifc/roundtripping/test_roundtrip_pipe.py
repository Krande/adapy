import ada


def test_roundtrip_pipe_straight(tmp_path):
    # NOTE: a catalog section name is used because a custom circular-hollow section (e.g.
    # Section("PSec", "PIPE", r=.., wt=..)) writes as IfcArbitraryProfileDefWithVoids whose
    # circular geometry the section reader can't yet interpret — a separate section-reader gap.
    pipe = ada.Pipe("Pipe1", [(0, 0, 0), (0, 0, 3.2)], "PIPE200x5")
    fp = (ada.Assembly() / (ada.Part("MyPart") / pipe)).to_ifc(tmp_path / "pipe.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    p: ada.Pipe = a.get_by_name("Pipe1")

    assert isinstance(p, ada.Pipe)
    assert p.parent.name == "MyPart"
    # one straight segment
    assert len(p.segments) == 1
    assert isinstance(p.segments[0], ada.PipeSegStraight)


def test_roundtrip_pipe_with_bends(tmp_path):
    points = [(0, 0, 0), (5, 0, 0), (5, 5, 0), (5, 5, 3)]
    pipe = ada.Pipe("BentPipe", points, "PIPE200x5")
    n_straight = sum(isinstance(s, ada.PipeSegStraight) for s in pipe.segments)
    n_elbow = sum(isinstance(s, ada.PipeSegElbow) for s in pipe.segments)

    fp = (ada.Assembly() / (ada.Part("MyPart") / pipe)).to_ifc(tmp_path / "bent.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    p: ada.Pipe = a.get_by_name("BentPipe")

    assert isinstance(p, ada.Pipe)
    # segment composition (straights + elbows) preserved
    assert sum(isinstance(s, ada.PipeSegStraight) for s in p.segments) == n_straight
    assert sum(isinstance(s, ada.PipeSegElbow) for s in p.segments) == n_elbow
