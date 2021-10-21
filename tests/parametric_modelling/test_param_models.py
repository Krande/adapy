import unittest

from common import build_test_simplestru_fem

from ada import Assembly
from ada.config import Settings
from ada.core.utils import roundoff
from ada.fem import Load, StepImplicit
from ada.param_models.basic_module import make_it_complex

test_dir = Settings.test_dir / "param_models"


class ParamModelsTestCase(unittest.TestCase):
    def test_basic_module_to_from_ifc(self):
        a = build_test_simplestru_fem(make_fem=False)
        a.to_ifc(test_dir / "param1.ifc")

        a2 = Assembly("ImportedParam")
        a2.read_ifc(test_dir / "param1.ifc")
        a2.to_ifc(test_dir / "param1_reimported.ifc")

    def test_to_fem(self):
        a = build_test_simplestru_fem()

        param_model = a.get_by_name("ParametricModel")
        param_model.fem.sections.merge_by_properties()

        a.to_ifc(test_dir / "my_simple_stru_weight.ifc")

        self.assertEqual(len(param_model.fem.bcs), 1)
        self.assertEqual(len(param_model.fem.elements), 11720)
        self.assertAlmostEqual(len(param_model.fem.nodes), 5331, delta=80)

        cog = param_model.fem.elements.calc_cog()
        tol = 0.01

        my_step = a.fem.add_step(StepImplicit("static", total_time=1, max_incr=1, init_incr=1, nl_geom=True))
        my_step.add_load(Load("Gravity", "gravity", -9.81))

        a.to_fem("SimpleStru", fem_format="usfos", overwrite=True)

        self.assertLess(abs(roundoff(cog.p[0]) - 2.5), tol)
        self.assertLess(abs(roundoff(cog.p[1]) - 2.5), tol)
        self.assertLess(abs(roundoff(cog.p[2]) - 1.5), tol)
        self.assertLess(abs(roundoff(cog.tot_mass) - 6672.406), tol)
        self.assertLess(abs(roundoff(cog.tot_vol) - 0.85), tol)

    def test_add_piping(self):
        a = make_it_complex()
        a.to_ifc(test_dir / "my_simple_stru_w_piping.ifc")


if __name__ == "__main__":
    unittest.main()
