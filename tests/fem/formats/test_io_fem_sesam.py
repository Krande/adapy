import unittest

from common import build_test_beam_fem

from ada.config import Settings

test_folder = Settings.test_dir / "sesam"


class TestSesam(unittest.TestCase):
    def test_simple_beam_fem_shell(self):
        a = build_test_beam_fem("shell")
        a.to_fem("beam_sh", "sesam", overwrite=True)

    def test_simple_beam_fem_line(self):
        a = build_test_beam_fem("line")
        a.to_fem("beam_line", "sesam", overwrite=True)

    def test_write_ff(self):
        from ada.fem.formats.sesam.writer import write_ff

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
    def test_write_usfos_bm(self):
        a = build_test_beam_fem("line")
        a.to_fem("my_usfos_bm_shell", fem_format="usfos", overwrite=True)
        # a.to_fem("my_xdmf_plate", "xdmf", overwrite=True, scratch_dir=test_folder, fem_converter="meshio")

    def test_write_usfos_sh(self):
        a = build_test_beam_fem("shell")
        a.to_fem("my_usfos_bm_line", fem_format="usfos", overwrite=True)
        # a.to_fem("my_xdmf_plate", "xdmf", overwrite=True, scratch_dir=test_folder, fem_converter="meshio")


if __name__ == "__main__":
    unittest.main()
