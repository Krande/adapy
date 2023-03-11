import pytest


@pytest.fixture
def cantilever_dir(fem_files):
    return fem_files / "cantilever"
