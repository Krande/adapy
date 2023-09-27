import copy

import pytest

from ada import Section
from ada.api.containers import Sections


@pytest.fixture
def sec():
    return Section("MyBGSec", from_str="BG800x400x30x40")


@pytest.fixture
def sec2():
    return Section("MyBGSec2", from_str="BG800x400x30x50")


@pytest.fixture
def secvar():
    return dict(sec_type="IG", h=0.8, w_top=0.2, w_btn=0.2, t_fbtn=0.01, t_ftop=0.01, t_w=0.01)


@pytest.fixture
def sec1(secvar):
    return Section(name="sec1", **secvar)


def test_section_equal(sec1, secvar):
    sec2 = copy.deepcopy(sec1)
    sec2.name = "sec2"
    sec3 = Section(name="sec3", **secvar)
    list_of_secs = [sec1, sec2, sec3]

    assert sec1 == sec1
    assert sec1 in list_of_secs
    assert Section(name="sec4", **secvar) not in list_of_secs


def test_negative_contained(sec, sec2):
    sec_collection = Sections([sec])
    assert sec2 not in sec_collection


def test_positive_contained(sec, sec2):
    sec_collection = Sections([sec, sec2])
    assert sec2 in sec_collection
