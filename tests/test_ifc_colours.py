import unittest

from ada import Assembly, Beam, Part
from ada.config import Settings
from ada.core.constants import color_map

test_folder = Settings.test_dir / "colours"


class MyColourTestCases(unittest.TestCase):
    def test_coloured_beams(self):
        beams = []
        a = 0
        for color_name, color in color_map.items():
            beams += [Beam(f"bm{a}", (a, a, a), (a + 1, a + 1, a + 1), "TUB300/200x20", colour=color_name)]
            a += 1
            beams += [Beam(f"bm{a}", (a, a, a), (a + 1, a + 1, a + 1), "TUB300/200x20", colour=color)]
            a += 1

        a = Assembly("SiteTest", project="projA", creator="krande") / (Part("TestBldg") / beams)
        a.to_ifc(test_folder / "colours.ifc")


if __name__ == "__main__":
    unittest.main()
