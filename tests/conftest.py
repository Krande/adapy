import pathlib

import pytest

import ada

is_printed = False
TESTS_DIR = pathlib.Path(__file__).resolve().absolute().parent
ROOT_DIR = TESTS_DIR.parent
print(TESTS_DIR)
print(ROOT_DIR)


@pytest.fixture
def this_dir() -> pathlib.Path:
    return TESTS_DIR


@pytest.fixture
def root_dir() -> pathlib.Path:
    return ROOT_DIR


@pytest.fixture
def example_files(this_dir) -> pathlib.Path:
    return ROOT_DIR / "files"


@pytest.fixture
def fem_files(example_files) -> pathlib.Path:
    return example_files / "fem_files"


@pytest.fixture
def test_shell_beam() -> ada.Assembly:
    bm = ada.Beam("Bm", (0, 0, 0), (1, 0, 0), "IPE300")
    return ada.Assembly("MyAssembly") / (ada.Part("MyPart", fem=bm.to_fem_obj(0.1, "shell")) / bm)


@pytest.fixture
def plate1():
    return ada.Plate("MyPlate", [(0, 0), (1, 0), (1, 1), (0, 1)], 20e-3)


@pytest.fixture
def bm_ipe300():
    return ada.Beam("MyIPE300", (0, 0, 0), (5, 0, 0), "IPE300")


@pytest.fixture
def basic_2d_plate():
    return ada.Plate(
        "MyPl",
        [(0, 0, 0.2), (5, 0), (5, 5), (0, 5)],
        20e-3,
        placement=ada.Placement(origin=(0, 0, 0), xdir=(1, 0, 0), zdir=(0, 0, 1)),
    )


@pytest.fixture
def pipe_sec() -> ada.Section:
    return ada.Section("PSec", "PIPE", r=0.10, wt=5e-3)


@pytest.fixture
def pipe_w_multiple_bends(pipe_sec) -> ada.Pipe:
    z = 3.2
    y0 = -200e-3
    x0 = -y0
    coords = [
        (0, y0, z),
        (5 + x0, y0, z),
        (5 + x0, y0 + 5, z),
        (10, y0 + 5, z + 2),
        (10, y0 + 5, z + 10),
    ]
    pipe1 = ada.Pipe(
        "Pipe1",
        coords,
        pipe_sec,
    )
    return pipe1


@pytest.fixture
def mixed_model(pipe_w_multiple_bends, basic_2d_plate):
    bm1 = ada.Beam("bm1", (0, 0, 0), (1, 0, 0), "HP140x8")
    bm2 = ada.Beam("bm2", (0, 1, 0), (1, 1, 0), "HP140x8")
    bm3 = ada.Beam("bm3", (0, 2, 0), (1, 2, 0), "HP140x8")

    mix1 = [bm1, pipe_w_multiple_bends]
    mix2 = [bm2, basic_2d_plate]

    return ada.Assembly() / [(ada.Part("P1") / mix1), (ada.Part("P2") / mix2), (ada.Part("P3") / bm3)]
