"""Effective modal mass / participation factors (global axes).

Covers:
  * Calculix ``.dat`` parsing — effective modal mass must come from the
    effective-mass block, not the participation block (regression for the
    ``read_eigen_data`` row mix-up).
  * Code_Aster IMPR_TABLE CSV parsing (translational DX/DY/DZ only).
  * ``calc_tot_eff_mass`` is None-safe (Code_Aster reports no rotational
    effective mass, so those components stay None).
"""

from ada.fem.formats.calculix.results.read_eigen_data import get_eigen_data
from ada.fem.formats.code_aster.results.results import _read_modal_mass_csv
from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

# Minimal Calculix .dat with distinct participation vs effective-mass
# values so a row mix-up is detectable. Section headers match what the
# reader looks for once whitespace is stripped (e.g. "effectivemodalmass").
_CCX_DAT = """
 E I G E N V A L U E   O U T P U T

 MODE NO   EIGENVALUE     OMEGA        FREQUENCY       IMAG
      1   0.1000000E+04   0.3162278E+02   0.5032922E+01   0.0000000E+00
      2   0.4000000E+04   0.6324555E+02   0.1006584E+02   0.0000000E+00

 P A R T I C I P A T I O N   F A C T O R S

 MODE NO   X            Y            Z            RX           RY           RZ
      1   0.1000000E+01   0.2000000E+01   0.3000000E+01   0.4000000E+01   0.5000000E+01   0.6000000E+01
      2   0.1100000E+02   0.1200000E+02   0.1300000E+02   0.1400000E+02   0.1500000E+02   0.1600000E+02

 E F F E C T I V E   M O D A L   M A S S

 MODE NO   X            Y            Z            RX           RY           RZ
      1   0.7000000E+02   0.8000000E+02   0.9000000E+02   0.1000000E+03   0.1100000E+03   0.1200000E+03
      2   0.1300000E+03   0.1400000E+03   0.1500000E+03   0.1600000E+03   0.1700000E+03   0.1800000E+03

 T O T A L   E F F E C T I V E   M A S S

      0.2000000E+03   0.2200000E+03   0.2400000E+03   0.2600000E+03   0.2800000E+03   0.3000000E+03
"""


def test_ccx_eigen_dat_effective_mass_not_participation(tmp_path):
    dat = tmp_path / "eig.dat"
    dat.write_text(_CCX_DAT)

    summary = get_eigen_data(dat)
    assert [m.no for m in summary.modes] == [1, 2]

    m1 = summary.modes[0]
    # participation X/Y/Z come from the participation block
    assert (m1.px, m1.py, m1.pz) == (1.0, 2.0, 3.0)
    # effective mass X/Y/Z come from the effective-mass block (NOT a copy
    # of participation — the regression this guards)
    assert (m1.efx, m1.efy, m1.efz) == (70.0, 80.0, 90.0)
    assert m1.efx != m1.px

    # values are floats, not raw Fortran strings
    assert all(isinstance(v, float) for v in (m1.px, m1.efx, m1.efrz))
    assert summary.tot_eff_mass == [200.0, 220.0, 240.0, 260.0, 280.0, 300.0]


_CA_CSV = """#ASTER 14.04.00 CONCEPT tab_modes CALCULE LE
NUME_MODE,FREQ,MASS_EFFE_DX,MASS_EFFE_DY,MASS_EFFE_DZ,FACT_PARTICI_DX,FACT_PARTICI_DY,FACT_PARTICI_DZ
1,1.31460E+01,5.72633E-28,1.19923E+02,3.53033E-25,2.39298E-14,1.09509E+01,-5.94165E-13
2,8.22500E+01,4.10000E+02,1.10000E-20,2.00000E-24,2.02000E+01,1.00000E-10,1.00000E-12
"""


def test_ca_modal_mass_csv_parse(tmp_path):
    csv = tmp_path / "x.modalmass.csv"
    csv.write_text(_CA_CSV)

    rows = _read_modal_mass_csv(csv)
    assert set(rows) == {1, 2}
    assert rows[1]["MASS_EFFE_DY"] == 119.923
    assert rows[1]["FACT_PARTICI_DY"] == 10.9509
    assert rows[2]["MASS_EFFE_DX"] == 410.0
    # NUME_MODE is the key, not a data column
    assert "NUME_MODE" not in rows[1]


def test_read_modal_mass_csv_missing_file(tmp_path):
    assert _read_modal_mass_csv(tmp_path / "nope.csv") == {}


def test_calc_tot_eff_mass_is_none_safe():
    # Code_Aster gives translational effective mass only; rotational stays None.
    summary = EigenDataSummary(
        modes=[
            EigenMode(1, f_hz=10.0, efx=1.0, efy=2.0, efz=3.0),
            EigenMode(2, f_hz=20.0, efx=4.0, efy=5.0, efz=6.0),
        ]
    )
    assert summary.calc_tot_eff_mass() == [5.0, 7.0, 9.0, 0.0, 0.0, 0.0]
