import os
import pathlib

import pytest

from ada.config import Settings


@pytest.fixture
def this_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().absolute().parent


@pytest.fixture
def test_dir() -> pathlib.Path:
    testing_dir = Settings.test_dir
    os.makedirs(testing_dir, exist_ok=True)
    return testing_dir


@pytest.fixture
def example_files(this_dir) -> pathlib.Path:
    return this_dir / ".." / "files"
