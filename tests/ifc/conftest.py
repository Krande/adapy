import pytest

from ada.config import Settings


@pytest.fixture
def ifc_test_dir():
    return Settings.test_dir / "ifc"
