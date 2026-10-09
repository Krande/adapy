"""A failed solve is named by the runner: the solver's message code, the file that says so, what would fix it.

The texts are the solvers' own (Code_Aster 18.1.8 .mess, CalculiX 2.23 standard output), cut down; the solve tests
in ``tests/fem/test_calculix_code_aster_solve.py`` provoke the same failures in the solvers.
"""

from __future__ import annotations

import subprocess

import pytest

_BAR = "║"
_FACTOR_11 = f"""
 ╔{"=" * 96}╗
 {_BAR} <EXCEPTION> <FACTOR_11>{" " * 72}{_BAR}
 {_BAR}{" " * 96}{_BAR}
 {_BAR} Problème : la matrice est singulière ou presque singulière :{" " * 35}{_BAR}
 {_BAR}   (pivot nul ou presque nul) à la ligne 29 qui correspond{" " * 38}{_BAR}
 ╚{"=" * 96}╝
"""


def _mess(tmp_path, body: str, verdict: str | None):
    path = tmp_path / "job.mess"
    tail = "" if verdict is None else f"\n{'-' * 60}\n--- DIAGNOSTIC JOB : {verdict}\n{'-' * 60}\n"
    path.write_text("  -- CODE_ASTER -- VERSION : 18.1.8\n" + body + tail, encoding="utf-8")
    return path


@pytest.mark.parametrize("verdict", ["OK", "<A>_ALARM"])
def test_code_aster_ok_and_alarm_runs_pass(tmp_path, verdict):
    from ada.fem.formats.code_aster.execute import check_code_aster_run

    check_code_aster_run(_mess(tmp_path, "", verdict))


def test_code_aster_error_is_raised_by_its_message_code(tmp_path):
    """``<S>_ERROR`` with a framed ``<EXCEPTION> <FACTOR_11>`` (measured: a beam held in dz only) raises
    ``FEASolveFailed`` with that code, the .mess path, the message text and the known cause."""
    from ada.fem.exceptions.fea_execution import FEASolveFailed
    from ada.fem.formats.code_aster.execute import check_code_aster_run

    # Code_Aster prints the frame twice (at the command and again with the traceback): one code
    path = _mess(tmp_path, _FACTOR_11 * 2, "<S>_ERROR")
    with pytest.raises(FEASolveFailed) as info:
        check_code_aster_run(path)
    err = info.value
    assert (err.solver, err.code, err.codes, err.file) == ("code_aster", "FACTOR_11", ("FACTOR_11",), path)
    assert "la matrice est singulière" in err.message and "<S>_ERROR" in err.message
    assert "mechanism" in err.hint
    assert "<FACTOR_11>" in str(err) and str(path) in str(err)


def test_code_aster_without_a_verdict_or_a_message_file_is_a_failure(tmp_path):
    from ada.fem.exceptions.fea_execution import FEASolveFailed
    from ada.fem.formats.code_aster.execute import check_code_aster_run

    with pytest.raises(FEASolveFailed) as info:
        check_code_aster_run(_mess(tmp_path, "", None))
    assert info.value.code == "NO_DIAGNOSTIC"
    with pytest.raises(FEASolveFailed) as info:
        check_code_aster_run(tmp_path / "absent.mess")
    assert info.value.code == "NO_MESS"


def _ccx(stdout: str, returncode: int = 0):
    return subprocess.CompletedProcess(["ccx"], returncode, stdout=stdout, stderr="")


def test_calculix_finished_run_passes(tmp_path):
    from ada.fem.formats.calculix.execute import check_calculix_run

    check_calculix_run(_ccx(" *WARNING in calinput: PEEQ-output requested\n\n Job finished\n"), tmp_path / "log.txt")
    assert not (tmp_path / "log.txt").exists()


def test_calculix_error_line_is_raised_by_its_routine(tmp_path):
    """ccx 2.23's own lines: a load on an undefined node, then calinput's stop."""
    from ada.fem.exceptions.fea_execution import FEASolveFailed
    from ada.fem.formats.calculix.execute import check_calculix_run

    out = (
        " *ERROR reading *CLOAD: node        99999\n        is not defined\n"
        " *WARNING reading *NODE/EL/CONTACT FILE: label not applicable\n"
        " *ERROR in calinput: at least one fatal\n        error message while reading the\n"
        "        input deck: CalculiX stops.\n"
    )
    with pytest.raises(FEASolveFailed) as info:
        check_calculix_run(_ccx(out, 201), tmp_path / "log.txt")
    err = info.value
    assert (err.solver, err.code, err.codes) == ("calculix", "reading_CLOAD", ("reading_CLOAD", "calinput"))
    assert err.message == "*ERROR reading *CLOAD: node        99999 is not defined"
    assert (tmp_path / "log.txt").read_text() == out


def test_calculix_singular_matrix_is_read_from_spooles_out(tmp_path):
    """A mechanism: ccx's output stops after "Factoring the system of equations" (no ``Job finished``, no
    ``*ERROR``) and only ``spooles.out`` says why."""
    from ada.fem.exceptions.fea_execution import FEASolveFailed
    from ada.fem.formats.calculix.execute import check_calculix_run

    (tmp_path / "spooles.out").write_text("\n matrix found to be singular\n")
    out = " Factoring the system of equations using the symmetric spooles solver\n Using 1 cpu for spooles.\n"
    with pytest.raises(FEASolveFailed) as info:
        check_calculix_run(_ccx(out), tmp_path / "log.txt", tmp_path / "spooles.out")
    assert info.value.code == "spooles_singular" and "mechanism" in info.value.hint
    (tmp_path / "spooles.out").unlink()
    with pytest.raises(FEASolveFailed) as info:
        check_calculix_run(_ccx(out, 3), tmp_path / "log.txt", tmp_path / "spooles.out")
    assert info.value.code == "no_job_finished" and "exit code 3" in info.value.message
