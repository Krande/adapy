import pytest


@pytest.fixture
def colored_flat_plate_step(example_files):
    return example_files / "step_files/flat_plate_abaqus_10x10_m_wColors.stp"


@pytest.fixture
def colored_assembly_step(example_files):
    return example_files / "step_files/as1-oc-214.stp"
