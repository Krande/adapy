import pytest


@pytest.fixture
def visualization_test_dir(test_dir):
    return test_dir / "visualization"
