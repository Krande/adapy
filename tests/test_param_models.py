import unittest

from ada import Assembly, Pipe, Section
from ada.config import Settings
from ada.fem import Load, Step
from ada.param_models.basic_module import SimpleStru

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

        a.to_fem("SimpleStru", fem_format="abaqus", overwrite=True)

    def test_add_piping(self):
        a = Assembly("ParametricSite")

        pm = SimpleStru("ParametricModel")
        a.add_part(pm)

        elev = pm.Params.h - 0.4
        offset_Y = 0.4
        pipe1 = Pipe(
            "Pipe1",
            [
                (0, offset_Y, elev),
                (pm.Params.w + 0.4, offset_Y, elev),
                (pm.Params.w + 0.4, pm.Params.l + 0.4, elev),
                (pm.Params.w + 0.4, pm.Params.l + 0.4, 0.4),
                (0, pm.Params.l + 0.4, 0.4),
            ],
            Section("PSec1", "PIPE", r=0.1, wt=10e-3),
        )

        pipe2 = Pipe(
            "Pipe2",
            [
                (0.5, offset_Y + 0.5, elev + 1.4),
                (0.5, offset_Y + 0.5, elev),
                (0.2 + pm.Params.w, offset_Y + 0.5, elev),
                (0.2 + pm.Params.w, pm.Params.l + 0.4, elev),
                (0.2 + pm.Params.w, pm.Params.l + 0.4, 0.6),
                (0, pm.Params.l + 0.4, 0.6),
            ],
            Section("PSec1", "PIPE", r=0.05, wt=5e-3),
        )

        pm.add_pipe(pipe1)
        pm.add_pipe(pipe2)
        for p in pm.parts.values():
            if "floor" in p.name:
                p.penetration_check()

        a.to_ifc(test_folder / "my_simple_stru.ifc")


if __name__ == "__main__":
    unittest.main()
