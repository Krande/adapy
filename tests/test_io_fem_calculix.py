import unittest

from common import build_test_simplestru_fem, example_files

from ada import Assembly
from ada.fem import FemSet, Load, Step


class TestCalculix(unittest.TestCase):
    def test_read_C3D20(self):
        a = Assembly()
        a.read_fem(example_files / "fem_files/calculix/contact2e.inp")
        beams = list(a.parts.values())[0]
        vol = beams.fem.nodes.vol_cog
        assert vol == (0.49999999627471, 1.2499999925494, 3.9999999701977)

    def test_write_test_model(self):
        a = build_test_simplestru_fem()
        fs = a.fem.add_set(
            FemSet("Eall", [el for el in a.get_by_name("ParametricModel").fem.elements.elements], "elset")
        )

        my_step = Step("static", "static", total_time=1, max_incr=1, init_incr=1, nl_geom=True, restart_int=1)
        my_step.add_load(Load("Gravity", "gravity", -9.81, fem_set=fs))
        a.fem.add_step(my_step)

        a.to_fem("my_calculix", fem_format="calculix", overwrite=True)  # , execute=True, exit_on_complete=False)
