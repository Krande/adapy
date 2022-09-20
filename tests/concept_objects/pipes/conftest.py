import pytest

from ada import Pipe, Section


@pytest.fixture
def pipe_sec() -> Section:
    return Section("PSec", "PIPE", r=0.10, wt=5e-3)


@pytest.fixture
def pipe_w_single_90_deg_bend(pipe_sec) -> Pipe:
    pipe1 = Pipe(
        "pipe_single_90_deg_bend",
        [
            (0, 0, 0),
            (5, 0, 0),
            (5, 5, 0),
        ],
        pipe_sec,
    )
    return pipe1


@pytest.fixture
def pipe_w_multiple_bends(pipe_sec) -> Pipe:
    z = 3.2
    y0 = -200e-3
    x0 = -y0

    pipe1 = Pipe(
        "Pipe1",
        [
            (0, y0, z),
            (5 + x0, y0, z),
            (5 + x0, y0 + 5, z),
            (10, y0 + 5, z + 2),
            (10, y0 + 5, z + 10),
        ],
        pipe_sec,
    )
    return pipe1
