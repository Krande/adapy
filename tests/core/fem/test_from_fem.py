import pytest

import ada


@pytest.fixture
def plate_shell_fem_model():
    p = ada.Part("MyPart")
    pl = ada.Plate("pl", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01)
    p.fem = pl.to_fem_obj(0.1, "shell", use_quads=True)
    return ada.Assembly() / p


@pytest.fixture
def sesam_fem_plate_shell_file(plate_shell_fem_model, tmp_path):
    sesam_fem_dir = tmp_path / "my_fem"
    fem_file = sesam_fem_dir / "my_femT1.FEM"
    plate_shell_fem_model.to_fem("my_fem", fem_format="sesam", scratch_dir=tmp_path)

    yield fem_file


@pytest.fixture
def abaqus_fem_plate_shell_file(plate_shell_fem_model, tmp_path):
    sesam_fem_dir = tmp_path / "my_fem"
    fem_file = sesam_fem_dir / "my_fem.inp"
    plate_shell_fem_model.to_fem("my_fem", fem_format="abaqus", scratch_dir=tmp_path)

    yield fem_file


@pytest.mark.benchmark(group="fem")
def test_from_sesam_fem(sesam_fem_plate_shell_file):
    a = ada.from_fem(sesam_fem_plate_shell_file)

    assert len(a.get_all_subparts()) == 1
    p = a.get_all_subparts()[0]

    assert len(p.fem.elements) == 100

    p.create_objects_from_fem()

    assert len(p.plates) == 100


@pytest.mark.benchmark(group="fem")
def test_from_abaqus_fem(abaqus_fem_plate_shell_file):
    a = ada.from_fem(abaqus_fem_plate_shell_file)

    assert len(a.get_all_subparts()) == 1
    p = a.get_all_subparts()[0]

    assert len(p.fem.elements) == 100

    p.create_objects_from_fem()

    assert len(p.plates) == 100
