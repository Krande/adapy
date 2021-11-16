import pytest

from ada import Section
from ada.concepts.containers import Sections


@pytest.fixture
def sec():
    return Section("MyBGSec", from_str="BG800x400x30x40")


@pytest.fixture
def sec2():
    return Section("MyBGSec2", from_str="BG800x400x30x50")


def test_negative_contained(sec, sec2):
    sec_collection = Sections([sec])
    assert sec2 not in sec_collection


def test_positive_contained(sec, sec2):
    sec_collection = Sections([sec, sec2])
    assert sec2 in sec_collection
