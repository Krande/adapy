"""A failed BOPAlgo run must name OCCT's own alerts, not just say "reported errors".

``BOPAlgo_Options.HasErrors()`` is a boolean summary; the reason is a list of typed
alerts on the algorithm's ``Message_Report``. Raising only the summary makes every
distinct OCCT failure — a wrong operand count, a failed intersection, a null shape —
produce the identical string, so a report of one cannot be triaged without a
debugger. That is how the operand-count precondition fixed in #263 was first read as
a geometry-kernel problem.
"""

from __future__ import annotations

import pytest

from ada.occ.backend import _bop_alert_names, _bop_error


class _Exploding:
    """An algorithm whose report cannot be read at all."""

    def GetReport(self):
        raise RuntimeError("no report available")


def test_alert_names_never_raise_from_the_error_path():
    # A diagnostic must not replace the failure it is describing.
    assert _bop_alert_names(_Exploding()) == []


def test_error_without_alerts_keeps_the_bare_message():
    err = _bop_error("merge_cells", "BOPAlgo_CellsBuilder", _Exploding())

    assert str(err) == "merge_cells: BOPAlgo_CellsBuilder reported errors"
    assert isinstance(err, RuntimeError)


def _cells_builder(n_operands: int):
    pytest.importorskip("OCC")
    from OCC.Core.BOPAlgo import BOPAlgo_CellsBuilder
    from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCC.Core.TopTools import TopTools_ListOfShape

    cb = BOPAlgo_CellsBuilder()
    args = TopTools_ListOfShape()
    for i in range(n_operands):
        args.Append(BRepPrimAPI_MakeBox(1.0, 1.0, 1.0).Shape())
    cb.SetArguments(args)
    cb.Perform()
    return cb


@pytest.mark.parametrize("n_operands", [0, 1])
def test_too_few_arguments_is_named_in_the_message(n_operands):
    # CellsBuilder is a general fuse of >= 2 operands. This is the exact failure
    # behind #263 — and the name is what makes it self-evidently a precondition
    # rather than bad geometry.
    cb = _cells_builder(n_operands)
    assert cb.HasErrors()

    assert _bop_alert_names(cb) == ["BOPAlgo_AlertTooFewArguments"]
    assert str(_bop_error("merge_cells", "BOPAlgo_CellsBuilder", cb)) == (
        "merge_cells: BOPAlgo_CellsBuilder reported errors [BOPAlgo_AlertTooFewArguments]"
    )


def test_reading_the_report_leaves_it_intact():
    # The alert list is drained from a copy; a second read must still work, and the
    # algorithm's own error state must be unchanged.
    cb = _cells_builder(1)

    assert _bop_alert_names(cb) == ["BOPAlgo_AlertTooFewArguments"]
    assert _bop_alert_names(cb) == ["BOPAlgo_AlertTooFewArguments"]
    assert cb.HasErrors()


def test_successful_run_reports_no_alerts():
    cb = _cells_builder(2)

    assert not cb.HasErrors()
    assert _bop_alert_names(cb) == []
