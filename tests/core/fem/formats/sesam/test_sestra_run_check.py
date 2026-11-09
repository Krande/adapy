"""``check_sestra_run``: a "successful" Sestra run that solved none of the deck's load cases is a failure.

Measured with Sestra 11.3-01: a load written as a record Sestra does not take (BRIGVE) left the case empty; SESTRA.MLG
said "Number of load cases: 0" and "No load is specified", and still "Execution completed successfully".
"""

from __future__ import annotations

import pytest

from ada.fem.exceptions.fea_execution import FEASolveFailed
from ada.fem.formats.sesam.execute import check_sestra_run


def _run(tmp_path, cases: int, tdload: bool = True):
    (tmp_path / "SESTRA.MLG").write_text(
        f"INFO\nNumber of load cases: {cases}\nWARNING\nNo load is specified.\n\nExecution completed successfully.\n"
    )
    (tmp_path / "xT1.FEM").write_text("TDLOAD    4.00000000E+00  1.00000000E+00\n" if tdload else "IEND\n")
    return tmp_path


def test_a_deck_with_load_cases_and_none_solved_fails(tmp_path):
    with pytest.raises(FEASolveFailed, match="Number of load cases: 0"):
        check_sestra_run(_run(tmp_path, 0))


def test_solved_cases_or_a_deck_without_cases_pass(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    check_sestra_run(_run(tmp_path / "a", 1))
    check_sestra_run(_run(tmp_path / "b", 0, tdload=False))
