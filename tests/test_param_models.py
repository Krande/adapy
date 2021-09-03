import unittest

from ada import Assembly
from ada.config import Settings
from ada.fem import Load, Step
from ada.param_models.basic_module import SimpleStru, make_it_complex

test_folder = Settings.test_dir / "param_models"


class ParamModelsTestCase(unittest.TestCase):
    def test_basic_module_to_from_ifc(self):
        a = Assembly("ParametricSite")
        a.add_part(SimpleStru("ParametricModel"))
        a.to_ifc(test_folder / "param1.ifc")

        a2 = Assembly("ImportedParam")
        a2.read_ifc(test_folder / "param1.ifc")
        a2.to_ifc(test_folder / "param1_reimported.ifc")

    def test_basic_module_to_step(self):
        a = Assembly("ParametricSite")
        a.add_part(SimpleStru("ParametricModel"))
        # a.to_stp('param1', geom_type='solid')

    def test_to_fem(self):
        param_model = SimpleStru("ParametricModel")
        param_model.gmsh.mesh(order=1, size=0.1, max_dim=2, interactive=False)
        param_model.add_bcs()
        assert len(param_model.fem.bcs) == 4
        assert len(param_model.fem.elements) == 10420
        assert len(param_model.fem.nodes) == 5318

        a = Assembly("ParametricSite")
        a.add_part(param_model)

        my_step = Step("static", "static", total_time=1, max_incr=1, init_incr=1, nl_geom=True)
        my_step.add_load(Load("Gravity", "gravity", -9.81))
        a.fem.add_step(my_step)

        a.to_fem("SimpleStru", fem_format="abaqus", overwrite=False, execute=True)

    def test_add_piping(self):
        a = make_it_complex()
        a.to_ifc(test_folder / "my_simple_stru_w_piping.ifc")


if __name__ == "__main__":
    unittest.main()
