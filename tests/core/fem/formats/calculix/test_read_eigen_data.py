"""CalculiX reports S4 participation factors 3x too large; the reader
corrects a pure-S4 model and leaves everything else as reported."""

import logging

import pytest

from ada.config import logger
from ada.fem.formats.calculix.results.read_eigen_data import get_eigen_data

# Trimmed from a CalculiX 2.23 run: a 1 x 0.5 x 0.01 m steel plate (39.25 kg)
# of 2x1 S4 shells, clamped along one short edge.
DAT = """\
                        S T E P       1


     E I G E N V A L U E   O U T P U T

 MODE NO    EIGENVALUE                       FREQUENCY
                                     REAL PART            IMAGINARY PART
                           (RAD/TIME)      (CYCLES/TIME     (RAD/TIME)

      1   0.3344080E+04   0.5782802E+02   0.9203615E+01   0.0000000E+00
      2   0.3936684E+06   0.6274300E+03   0.9985859E+02   0.0000000E+00

     P A R T I C I P A T I O N   F A C T O R S

MODE NO.   X-COMPONENT     Y-COMPONENT     Z-COMPONENT     X-ROTATION      Y-ROTATION      Z-ROTATION

      1   0.9044744E-13   0.3473571E-12  -0.1429627E+02  -0.3574067E+01   0.1010849E+02   0.2355637E-12
      2  -0.1264810E-12  -0.5187715E-12  -0.6222345E+01  -0.1555586E+01   0.2481833E+01  -0.3657413E-12

     E F F E C T I V E   M O D A L   M A S S

MODE NO.   X-COMPONENT     Y-COMPONENT     Z-COMPONENT     X-ROTATION      Y-ROTATION      Z-ROTATION

      1   0.8180740E-26   0.1206570E-24   0.2043833E+03   0.1277396E+02   0.1021817E+03   0.5549025E-25
      2   0.1599744E-25   0.2691238E-24   0.3871758E+02   0.2419849E+01   0.6159494E+01   0.1337667E-24
TOTAL     0.2574775E+03   0.2824512E+03   0.2431009E+03   0.1567132E+02   0.1083412E+03   0.1249744E+03

     T O T A L   E F F E C T I V E   M A S S

           X-COMPONENT     Y-COMPONENT     Z-COMPONENT     X-ROTATION      Y-ROTATION      Z-ROTATION

          0.3009167E+03   0.3009167E+03   0.3030972E+03   0.1953436E+02   0.1112086E+03   0.1305608E+03
"""


def _element(el_type: str, elset: str = "EALL") -> str:
    return f"*ELEMENT, type={el_type}, ELSET={elset}\n1, 1, 2, 5, 4\n"


def _job(tmp_path, *elements: str):
    dat = tmp_path / "plate.dat"
    dat.write_text(DAT)
    if elements:
        (tmp_path / "plate.inp").write_text("*NODE\n" + "".join(elements))
    return dat


def test_s4_participation_is_scaled_down(tmp_path):
    summary = get_eigen_data(_job(tmp_path, _element("S4", "a"), _element("S4", "b")))
    mode1 = summary.modes[0]

    assert mode1.pz == pytest.approx(-14.29627 / 3)
    assert mode1.pry == pytest.approx(10.10849 / 3)
    assert mode1.efz == pytest.approx(204.3833 / 9)
    assert summary.tot_eff_mass[2] == pytest.approx(303.0972 / 9)
    # The modal mass fraction is untouched by the correction, and with it
    # mode 1 lands on the clamped plate's analytic 0.613 (coarse mesh).
    assert mode1.efz / 39.25 == pytest.approx(0.58, abs=0.01)
    # Frequencies are reported correctly and stay as they are.
    assert mode1.f_hz == pytest.approx(9.203615)
    assert mode1.eigenvalue == pytest.approx(3344.080)


@pytest.mark.parametrize("el_type", ["S4R", "S8", "S3", "C3D8"])
def test_other_elements_are_kept_as_reported(tmp_path, el_type):
    summary = get_eigen_data(_job(tmp_path, _element(el_type)))
    assert summary.modes[0].efz == pytest.approx(204.3833)
    assert summary.tot_eff_mass[2] == pytest.approx(303.0972)


def test_s4_mixed_with_other_elements_is_kept_and_warned(tmp_path, monkeypatch, caplog):
    dat = _job(tmp_path, _element("S4", "a"), _element("B31", "b"))
    # adapy's logger does not propagate to the root one caplog listens on: hand it caplog's handler.
    monkeypatch.setattr(logger, "handlers", [*logger.handlers, caplog.handler])
    with caplog.at_level(logging.WARNING, logger=logger.name):
        summary = get_eigen_data(dat)
    assert summary.modes[0].efz == pytest.approx(204.3833)
    assert "S4 participation factors" in caplog.text


def test_without_deck_values_are_kept(tmp_path):
    summary = get_eigen_data(_job(tmp_path))
    assert summary.modes[0].pz == pytest.approx(-14.29627)
