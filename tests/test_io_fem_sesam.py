import unittest

from common import build_test_simplestru_fem

from ada import Assembly, Beam, Plate


class TestSesam(unittest.TestCase):
    def test_write_simple_stru(self):
        from ada.fem.meshing.gmshapiv2 import GmshSession
        from ada.param_models.basic_module import SimpleStru

        a = Assembly("MyTest")
        p = a.add_part(SimpleStru("SimpleStru"))

        with GmshSession(silent=True) as gs:
            gmap = dict()
            for obj in p.get_all_physical_objects():
                if type(obj) is Beam:
                    li = gs.add_obj(obj, geom_repr="line")
                    gmap[obj] = li
                elif type(obj) is Plate:
                    pl = gs.add_obj(obj, geom_repr="shell")
                    gmap[obj] = pl
            gs.mesh()
            # gs.open_gui()
            p.fem = gs.get_fem()

        # TODO: Support mixed plate and beam models. Ensure nodal connectivity
        a.to_fem("MySesamStru", fem_format="sesam", overwrite=True)

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
        a = build_test_simplestru_fem()
        a.to_fem("my_usfos", fem_format="usfos", overwrite=True)


if __name__ == "__main__":
    unittest.main()
