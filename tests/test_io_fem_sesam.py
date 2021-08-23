import unittest

from common import build_test_model

from ada import Assembly


class TestSesam(unittest.TestCase):
    def test_write_simple_stru(self):
        from ada.param_models.basic_module import SimpleStru

        a = Assembly("MyTest")
        p = a.add_part(SimpleStru("SimpleStru"))
        p.gmsh.mesh()
        a.to_fem("MyTest", fem_format="sesam", overwrite=True)

    def test_write_ff(self):
        from ada.fem.io.sesam.writer import write_ff

        flag = "TDMATER"
        data = [
            (1, 1, 0, 0),
            (83025, 4, 0, 3),
            (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558),
        ]
        test_str = write_ff(flag, data)
        fflag = "BEUSLO"
        ddata = [
            (1, 1, 0, 0),
            (83025, 4, 0, 3),
            (0.4870624787676558, 0.4870624787676558, 0.4870624787676558, 0.4870624787676558),
        ]
        test_str += write_ff(fflag, ddata)
        print(test_str)


class TestUsfos(unittest.TestCase):
    def test_write_usfos(self):
        a = build_test_model()
        a.to_fem("my_usfos", fem_format="usfos", overwrite=True)


if __name__ == "__main__":
    unittest.main()
