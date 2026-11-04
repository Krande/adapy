"""``check_sestra_run`` reads Sestra's verdict out of SESTRA.MLG / SESTRA.LIS.

Sestra exits with code 0 and leaves an ``R1.SIN`` behind when the analysis fails, so the logs are the only verdict.
The texts below are the tails of real V11.3-01 logs. Runs without Sestra; tests/fem/test_sesam_point_mass_on_solid.py
covers a real failed run.
"""

from __future__ import annotations

import pytest

from ada.fem.exceptions.fea_execution import FEASolveFailed
from ada.fem.formats.execute import check_previous_run
from ada.fem.formats.general import FEATypes
from ada.fem.formats.sesam.execute import check_sestra_run

FAILED_MLG = """INFO, Wed Nov  4 12:25:28 2026
Analysis failed.

ERROR, Wed Nov  4 12:25:28 2026
Execution failed!
    Invalid argument to element library routine. GmasWrapper: Illegal input to mass matrix routine

"""

OK_MLG = """INFO, Wed Nov  4 12:25:32 2026
Execution completed successfully.
    Elapsed wall-clock time: 0.339455 seconds.

INFO, Wed Nov  4 12:25:32 2026
Sestra analysis finished.
"""


def test_successful_run_passes(tmp_path):
    (tmp_path / "SESTRA.MLG").write_text(OK_MLG)
    check_sestra_run(tmp_path)


def test_normal_exit_in_the_lis_alone_passes(tmp_path):
    (tmp_path / "SESTRA.LIS").write_text(" * Normal exit from Sestra.\n")
    check_sestra_run(tmp_path)


def test_failed_run_raises_with_the_mlg_error(tmp_path):
    (tmp_path / "SESTRA.MLG").write_text(FAILED_MLG)
    (tmp_path / "SESTRA.LIS").write_text(" * ERROR exit from Sestra. See Sestra.MLG for details.\n")
    with pytest.raises(FEASolveFailed) as err:
        check_sestra_run(tmp_path)
    assert err.value.solver == "sestra"
    assert err.value.code == "execution_failed"
    assert err.value.file == tmp_path / "SESTRA.MLG"
    assert err.value.message == (
        "Execution failed! Invalid argument to element library routine. GmasWrapper: Illegal input to mass matrix "
        "routine"
    )


def test_no_log_at_all_raises(tmp_path):
    with pytest.raises(FEASolveFailed) as err:
        check_sestra_run(tmp_path)
    assert err.value.code == "no_sestra_log"


def test_previous_run_with_a_failed_log_raises(tmp_path):
    """``to_fem(overwrite=False)`` must not read back the SIN a failed run left."""
    run_dir = tmp_path / "jt"
    run_dir.mkdir()
    (run_dir / "SESTRA.MLG").write_text(FAILED_MLG)
    with pytest.raises(FEASolveFailed):
        check_previous_run("jt", FEATypes.SESAM, tmp_path)


def test_previous_run_without_a_log_is_not_judged(tmp_path):
    """A SIN copied in, or from GeniE, has no Sestra log beside it: there is nothing to judge it by."""
    (tmp_path / "jt").mkdir()
    check_previous_run("jt", FEATypes.SESAM, tmp_path)
