import ada


def test_pipe_xyz():
    pipe = ada.Pipe("pipe", [(0, 0.5, 0), (1, 0.5, 0), (1.2, 0.7, 0.2), (1.5, 0.7, 0.2)], "OD120x6")
    assert len(pipe.segments) == 5
