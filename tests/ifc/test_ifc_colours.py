import pytest

from ada import Assembly, Beam, Part, User
from ada.core.constants import color_map


@pytest.fixture
def test_coulour_ifc(ifc_test_dir):
    return ifc_test_dir / "colours"


def test_coloured_beams(test_coulour_ifc):
    beams = []
    a = 0
    for color_name, color in color_map.items():
        beams += [Beam(f"bm{a}", (a, a, a), (a + 1, a + 1, a + 1), "TUB300/200x20", colour=color_name)]
        a += 1
        beams += [Beam(f"bm{a}", (a, a, a), (a + 1, a + 1, a + 1), "TUB300/200x20", colour=color)]
        a += 1

    a = Assembly("SiteTest", project="projA", user=User("krande")) / (Part("TestBldg") / beams)
    a.to_ifc(test_coulour_ifc / "colours.ifc")
