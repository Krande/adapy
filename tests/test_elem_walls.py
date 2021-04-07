import unittest

from ada import Assembly, Part, Wall
from ada.config import Settings
from ada.param_models.basic_structural_components import Door, Window

test_folder = Settings.test_dir / "walls"


class Walls(unittest.TestCase):
    def test_wall_simple(self):
        points = [(0, 0, 0), (5, 0, 0), (5, 5, 0)]
        w = Wall("MyWall", points, 3, 0.15, offset="LEFT")
        wi = Window("MyWindow1", 1.5, 1, 0.15)
        wi2 = Window("MyWindow2", 2, 1, 0.15)
        door = Door("Door1", 1.5, 2, 0.2)
        w.add_insert(wi, 0, 1, 1.2)
        w.add_insert(wi2, 1, 1, 1.2)
        w.add_insert(door, 0, 3.25, 0)

        a = Assembly("MyAssembly")
        p = Part("MyPart")
        a.add_part(p)
        p.add_wall(w)
        a.to_ifc(test_folder / "my_wall_wDoorsWindows.ifc")
        a._repr_html_()


if __name__ == "__main__":
    unittest.main()
