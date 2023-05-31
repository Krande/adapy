import pytest

from ada import Assembly, Beam, Part, User
from ada.visit.colors import color_dict


@pytest.fixture
def test_color_ifc(ifc_test_dir):
    return ifc_test_dir / "colors"


def test_coloured_beams(test_color_ifc):
    beams = []
    a = 0
    for color_name, color in color_dict.items():
        beams += [Beam(f"bm{a}", (a, a, a), (a + 1, a + 1, a + 1), "TUB300/200x20", color=color_name)]
        a += 1
        beams += [Beam(f"bm{a}", (a, a, a), (a + 1, a + 1, a + 1), "TUB300/200x20", color=color)]
        a += 1

    a = Assembly("SiteTest", project="projA", user=User("krande")) / (Part("TestBldg") / beams)
    _ = a.to_ifc(test_color_ifc / "colours.ifc", file_obj_only=True)
