import os
import pathlib
import unittest
from ada.core.utils import roundoff
from ada import Assembly, Part, Beam
from ada.param_models.basic_module import SimpleStru
from ada.fem import Step, Load, FemSet


this_dir = pathlib.Path(__file__).resolve().absolute().parent
example_files = this_dir / '..' / 'files'


def build_test_model():
    param_model = SimpleStru('ParametricModel')
    a = Assembly('ParametricSite')
    a.add_part(param_model)
    param_model.gmsh.mesh(max_dim=2, interactive=False)
    param_model.add_bcs()

    return a


def build_test_beam():
    a = Assembly('MyAssembly')
    p = Part('MyPart')
    p.add_beam(Beam('Bm', (0, 0, 0), (1, 0, 0), 'IPE300'))
    p.gmsh.mesh(0.5)
    a.add_part(p)
    return a


class TestCalculix(unittest.TestCase):
    def test_read_C3D20(self):
        from ada import Assembly
        a = Assembly('my_assembly', 'temp')
        a.read_fem(os.path.join(this_dir, example_files / 'fem_files/calculix/contact2e.inp'))
        beams = list(a.parts.values())[0]
        vol = beams.fem.nodes.vol_cog
        assert vol == (0.49999999627471, 1.2499999925494, 3.9999999701977)

    def test_write_test_model(self):
        a = build_test_model()
        fs = a.fem.add_set(
            FemSet('Eall', [el for el in a.get_by_name('ParametricModel').fem.elements.elements], 'elset'))

        my_step = Step('static', 'static', total_time=1, max_incr=1, init_incr=1, nl_geom=True, restart_int=1)
        my_step.add_load(Load('Gravity', 'gravity', -9.81, fem_set=fs))
        a.fem.add_step(my_step)

        a.to_fem('my_calculix', fem_format='calculix', overwrite=True)# , execute=True, exit_on_complete=False)


class TestCodeAster(unittest.TestCase):
    def test_write_bm(self):
        a = build_test_beam()
        a.to_fem('my_code_aster_bm', fem_format='code_aster', overwrite=True)

    def test_write_test_model(self):
        a = build_test_model()
        a.to_fem('simple_stru', fem_format='code_aster', overwrite=True)


class TestAbaqus(unittest.TestCase):
    def test_write_bm(self):
        a = build_test_beam()
        a.to_fem('my_beam', fem_format='abaqus', overwrite=True)

    def test_write_test_model(self):
        a = build_test_model()
        a.to_fem('my_abaqus', fem_format='abaqus', overwrite=True)

    def test_read_C3D20(self):
        from ada import Assembly
        a = Assembly('my_assembly', 'temp')
        a.read_fem(os.path.join(this_dir, example_files / 'fem_files/abaqus/box.inp'))

    def test_read_R3D4(self):
        from ada import Assembly
        a = Assembly('my_assembly', 'temp')
        a.read_fem(os.path.join(this_dir, example_files / 'fem_files/abaqus/box_rigid.inp'))
        assert len(a.fem.constraints) == 1


class TestSesam(unittest.TestCase):
    def test_write_simple_stru(self):
        from ada.param_models.basic_module import SimpleStru
        a = Assembly('MyTest')
        p = SimpleStru('SimpleStru')
        a.add_part(p)
        p.gmsh.mesh()
        a.to_fem('MyTest', fem_format='sesam', overwrite=True)

    def test_write_ff(self):
        from ada.fem.io.sesam.writer import SesamWriter

        flag = 'TDMATER'
        data = [(1, 1, 0, 0), (83025, 4, 0, 3),
                (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558)]
        test_str = SesamWriter.write_ff(flag, data)
        fflag = 'BEUSLO'
        ddata = [(1, 1, 0, 0), (83025, 4, 0, 3),
                 (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558)]
        test_str += SesamWriter.write_ff(fflag, ddata)
        print(test_str)


class TestUsfos(unittest.TestCase):
    def test_write_usfos(self):
        a = build_test_model()
        a.to_fem('my_usfos', fem_format='usfos', overwrite=True)


class TestMeshio(unittest.TestCase):
    def test_read_write_code_aster_to_xdmf(self):
        a = Assembly('meshio_from_ca', 'temp')
        a.read_fem(os.path.join(this_dir, example_files / 'fem_files/meshes/med/box.med'), fem_converter='meshio')
        a.to_fem('box_analysis_xdmf', fem_format='xdmf', fem_converter='meshio')

    def test_read_write_code_aster_to_abaqus(self):
        a = Assembly('meshio_from_ca', 'temp')
        a.read_fem(os.path.join(this_dir, example_files / 'fem_files/meshes/med/box.med'), fem_converter='meshio')
        a.to_fem('box_analysis_abaqus', fem_format='abaqus', fem_converter='meshio')

    def test_read_C3D20(self):
        from ada import Assembly
        a = Assembly('my_assembly', 'temp')
        a.read_fem(os.path.join(this_dir, example_files / 'fem_files/calculix/contact2e.inp'), fem_converter='meshio')

    def test_read_abaqus(self):
        b = Assembly('my_assembly', 'temp')
        b.read_fem(os.path.join(this_dir, example_files / 'fem_files/meshes/abaqus/element_elset.inp'),
                   fem_converter='meshio')

    def test_read_code_aster(self):
        a = Assembly('meshio_from_ca', 'temp')
        a.read_fem(os.path.join(this_dir, example_files / 'fem_files/meshes/med/box.med'), fem_converter='meshio')


class TestFemProperties(unittest.TestCase):
    def test_calc_cog(self):

        a = build_test_model()
        p = a.parts['ParametricModel']
        cog = p.fem.elements.calc_cog()

        tol = 0.01

        assert abs(roundoff(cog[0]) - 2.5) < tol
        assert abs(roundoff(cog[1]) - 2.5) < tol
        assert abs(roundoff(cog[2]) - 1.5) < tol
        assert abs(roundoff(cog[3]) - 7854.90) < tol
        assert abs(roundoff(cog[4]) - 1.001) < tol


if __name__ == '__main__':
    unittest.main()
