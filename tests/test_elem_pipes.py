import unittest

from ada import Assembly, Part, Pipe, Section
from ada.config import Settings

test_folder = Settings.test_dir / "pipes"


class PipeIO(unittest.TestCase):
    def test_pipe_straight(self):
        a = Assembly("MyTest")

        p = Part("MyPart")
        a.add_part(p)
        z = 3.2
        y0 = -200e-3
        pipe1 = Pipe("Pipe1", [(0, y0, 0), (0, y0, z)], Section("PSec", "PIPE", r=0.10, wt=5e-3))
        p.add_pipe(pipe1)
        a.to_ifc(test_folder / "pipe_straight.ifc")
        a._repr_html_()

    def test_pipe_bend(self):
        a = Assembly("MyTest")
        p = Part("MyPart")
        a.add_part(p)
        z = 3.2
        y0 = -200e-3
        x0 = -y0
        pipe1 = Pipe(
            "Pipe1",
            [
                (0, y0, z),
                (5 + x0, y0, z),
                (5 + x0, y0 + 5, z),
                (10, y0 + 5, z + 2),
                (10, y0 + 5, z + 10),
            ],
            Section("PSec", "PIPE", r=0.10, wt=5e-3),
        )
        p.add_pipe(pipe1)
        a.to_ifc(test_folder / "pipe_bend.ifc")
        a._repr_html_()


if __name__ == "__main__":
    unittest.main()
