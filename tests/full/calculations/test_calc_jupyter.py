from ada import Beam
from ada.calc.beams import BeamCalc


def test_basic_udl_in_jupyter():
    bm = Beam("MyBeam", (0, 0, 0), (1, 0, 0), "IPE300")
    udl = BeamCalc(bm)
    udl.add_distributed_load(-1e3)

    # This is only applicable if your have pytexit installed (which is no longer a dependency)
    # displ_latex = udl.get_displ_formula()
    # shear_latex = udl.get_shear_formula()
    # moment_latex = udl.get_moment_formula()
    #
    # assert displ_latex == "$$\\frac{w x \\left(L^3-2L x^2+x^3\\right)}{24E I}$$"
    # assert moment_latex == "$$\\frac{w x \\left(L-x\\right)}{2}$$"
    # assert shear_latex == "$$w \\left(\\frac{L}{2}-x\\right)$$"
    #
    # udl._repr_html_()
