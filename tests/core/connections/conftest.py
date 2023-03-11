import pytest

import ada


@pytest.fixture
def joints_test_dir():
    return ada.config.Settings.test_dir / "joints"
