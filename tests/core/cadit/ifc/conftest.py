import pytest


@pytest.fixture
def ifc_test_dir(test_dir):
    return test_dir / "ifc"
