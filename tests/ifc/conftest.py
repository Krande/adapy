import pytest

import ada


@pytest.fixture
def bm_ipe300():
    return ada.Beam("MyIPE300", (0, 0, 0), (5, 0, 0), "IPE300")


@pytest.fixture
def ifc_test_dir():
    return ada.config.Settings.test_dir / "ifc"
