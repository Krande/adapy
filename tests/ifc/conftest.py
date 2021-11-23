import pytest

import ada


@pytest.fixture
def ifc_test_dir(test_dir):
    return test_dir / "ifc"
