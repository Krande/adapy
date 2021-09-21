import unittest

from common import build_reinforced_floor

from ada import Beam, Plate
from ada.config import Settings
from ada.fem.meshing.concepts import GmshSession

test_folder = Settings.test_dir / "sesam"


class TestSesam(unittest.TestCase):
    def test_write_simple_stru(self):

        a = build_reinforced_floor()
        p = a.get_part("PartReinforcedPlate")
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
        a.to_fem("MySesamFloor", fem_format="sesam", overwrite=True)

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
        a = build_reinforced_floor()
        p = a.get_part("PartReinforcedPlate")
        p.fem = p.to_fem_obj(0.1, bm_repr="shell")
        a.to_fem("my_usfos", fem_format="usfos", overwrite=True)
        # a.to_fem("my_xdmf_plate", "xdmf", overwrite=True, scratch_dir=test_folder, fem_converter="meshio")


if __name__ == "__main__":
    unittest.main()
