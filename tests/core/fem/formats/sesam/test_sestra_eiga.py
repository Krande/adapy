"""Sestra's EIGA command on its fixed columns (Sestra UM 11.3.1, EIGA): ENR 6-10, NLVEC 21-25, ELOW 51-60, EHIGH
61-70, SHIFT 71-80. A value straddling two fields is read as two values -- a buckling shift of 0.001 across 67-71
came back from Sestra as EHIGH 0.00 and SHIFT 1."""

from __future__ import annotations

import pytest

from ada.fem.formats.sesam.write.write_steps import (
    BUCKLING_LANCZOS_PER_MODE,
    BUCKLING_SHIFT,
    EIGEN_SHIFT,
    buckling_nlvec,
    eiga_line,
    write_sestra_buckle_str,
    write_sestra_eig_str,
)
from ada.fem.steps import StepBuckling, StepEigen


def _fields(line: str) -> dict[str, str]:
    line = line.ljust(80)
    return {
        "cmd": line[0:5].strip(),
        "ENR": line[5:10].strip(),
        "ITMAX": line[15:20].strip(),
        "NLVEC": line[20:25].strip(),
        "EPS": line[25:30].strip(),
        "REPR": line[40:45].strip(),
        "ELOW": line[50:60].strip(),
        "EHIGH": line[60:70].strip(),
        "SHIFT": line[70:80].strip(),
        "gaps": (line[10:15] + line[30:40] + line[45:50]).strip(),
    }


def _eiga(text: str) -> str:
    (line,) = [ln for ln in text.splitlines() if ln.startswith("EIGA")]
    return line


@pytest.mark.parametrize("modes", [1, 9, 10, 100, 1000])
def test_the_eigen_eiga_holds_enr_and_its_shift_in_their_own_fields(modes):
    f = _fields(_eiga(write_sestra_eig_str("m", StepEigen("eig", num_eigen_modes=modes))))
    assert f["cmd"] == "EIGA" and f["ENR"] == f"{modes}."
    assert float(f["SHIFT"]) == EIGEN_SHIFT
    assert f["ELOW"] == f["EHIGH"] == f["NLVEC"] == f["gaps"] == ""


def test_the_buckling_eiga_holds_its_shift_and_lanczos_basis_in_their_own_fields():
    f = _fields(_eiga(write_sestra_buckle_str("m", StepBuckling("b", num_modes=4), free_dofs=1000)))
    assert f["ENR"] == "4." and float(f["SHIFT"]) == BUCKLING_SHIFT
    assert f["NLVEC"] == f"{BUCKLING_LANCZOS_PER_MODE * 4}."
    assert f["ELOW"] == f["EHIGH"] == f["gaps"] == ""


def test_the_lanczos_basis_falls_back_to_sestras_default_where_it_may_not_fit():
    # Sestra stops on NLVEC > n, so with too few free dofs (or none known) the field is left blank
    assert buckling_nlvec(4, free_dofs=15) is None and buckling_nlvec(4, free_dofs=None) is None
    assert buckling_nlvec(4, free_dofs=16) == 16
    assert _fields(_eiga(write_sestra_buckle_str("m", StepBuckling("b", num_modes=4), free_dofs=10)))["NLVEC"] == ""


def test_a_value_too_wide_for_its_field_is_refused():
    with pytest.raises(ValueError, match="does not fit"):
        eiga_line(100000, 0.0)
