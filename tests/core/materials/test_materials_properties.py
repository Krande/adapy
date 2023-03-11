from ada import Material
from ada.materials.metals import CarbonSteel


def test_main_properties():
    matS355 = Material("MatS355", mat_model=CarbonSteel("S355"))
    matS420 = Material("MatS420", mat_model=CarbonSteel("S420"))
    for model in [matS355.model, matS420.model]:
        assert model.E == 2.1e11
        assert model.rho == 7850
        assert model.v == 0.3

    assert matS355.model.sig_y == 355e6
    assert matS420.model.sig_y == 420e6
