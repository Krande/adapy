from ada import Beam
from ada.visualize.new_render_api import Visualize


def test_viz_beam(test_dir):
    viz = Visualize()
    viz.add_obj(Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE300"))
    # viz.display(off_screen_file=self.test_folder / "MyTest.svg")
