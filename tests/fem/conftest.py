import pytest

import ada
from ada.materials.metals import CarbonSteel, DnvGl16Mat


def beam() -> ada.Beam:
    return ada.Beam(
        "MyBeam",
        (0, 0.5, 0.5),
        (3, 0.5, 0.5),
        "IPE400",
        ada.Material("S420", CarbonSteel("S420", plasticity_model=DnvGl16Mat(15e-3, "S355"))),
    )


@pytest.fixture
def beam_fixture() -> ada.Beam:
    return beam()


@pytest.fixture
def short_name_map() -> dict:
    return dict(calculix="ccx", code_aster="ca", abaqus="aba", sesam="ses")
