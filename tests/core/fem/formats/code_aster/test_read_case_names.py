"""A Code_Aster result of load cases names its cases (``FEAResult.step_name_map``), as Sesam's reader names its result
cases. The MED file numbers a case by its order only; the name is read from the comment the writer puts above the
``MACRO_ELAS_MULT``. It used to be None, so a case could only be found by its position.

Solved in tests/fem/test_code_aster_case_stresses.py.
"""

from __future__ import annotations

from types import SimpleNamespace

from ada.fem.formats.code_aster.results.read_rmed_results import load_case_names

COMM = """
# Load cases of step concept_loads, by order number:
#   1: acc_x
#   2: acc_y
#   3: local_loads
result = MACRO_ELAS_MULT(
"""


def _fields(*names):
    return [SimpleNamespace(name=n) for n in names]


def test_case_names_come_from_the_comm_comment(tmp_path):
    (tmp_path / "jt.comm").write_text(COMM)
    names = load_case_names(
        tmp_path / "jt.rmed", _fields("result__DEPL[0] - 999.999", "result__REAC_NODA[2] - 999.999")
    )
    assert names == {1: "acc_x", 2: "acc_y", 3: "local_loads"}


def test_no_names_without_one_case_block_of_the_result(tmp_path):
    (tmp_path / "jt.comm").write_text(COMM + COMM.replace("concept_loads", "second"))
    assert load_case_names(tmp_path / "jt.rmed", _fields("result__DEPL")) is None
    (tmp_path / "jt.comm").write_text(COMM)
    assert load_case_names(tmp_path / "jt.rmed", _fields("result__DEPL", "result2__DEPL")) is None
    assert load_case_names(tmp_path / "none.rmed", _fields("result__DEPL")) is None
