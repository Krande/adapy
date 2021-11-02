import pytest


@pytest.fixture
def test_meshing_dir(test_dir):
    return test_dir / "meshing"
