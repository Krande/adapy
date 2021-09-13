import os
import unittest

from ada import Beam
from ada.config import Settings
from ada.visualize.new_render_api import Visualize


class VizApiV2(unittest.TestCase):
    def setUp(self) -> None:
        self.test_folder = Settings.test_dir / "new_viz_api"
        self.bm1 = Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE300")

    def test_viz_beam(self):
        os.makedirs(self.test_folder)
        viz = Visualize()
        viz.add_obj(self.bm1)
        viz.display(off_screen_file=self.test_folder / "MyTest.svg")


if __name__ == "__main__":
    unittest.main()
