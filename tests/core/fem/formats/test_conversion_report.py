"""The conversion report: what a conversion could not carry across, and how it says so.

Two properties matter more than the data structure, and both have bitten this project before:

*An omission must not be lost.* A writer that reports outside a collector must still log, because
that is what a library user sees.

*An omission must not be buried.* A conversion of an 81 MB deck once produced 1.1 million lines of
console output over a single defect; a report that logs per node rather than per construct would do
the same, and an engineer who cannot scroll to the top has not been told anything.
"""

from __future__ import annotations

import contextlib
import json
import logging

import numpy as np
import pytest

from ada.fem.formats import conversion_report
from ada.fem.formats.conversion_report import (
    APPROXIMATED,
    NOTE,
    OMITTED,
    STATUS_COMPLETED,
    STATUS_WITH_APPROXIMATIONS,
    STATUS_WITH_OMISSIONS,
    STATUS_WITH_SUSPECTS,
    ConversionReport,
    collect,
    current,
)


@contextlib.contextmanager
def _ada_logs(level: int = logging.DEBUG):
    """Collect records from the ``ada`` logger.

    Not ``caplog``: ``ada.config`` sets ``propagate = False`` on the ``ada`` logger, so its
    records never reach the root handler caplog installs.
    """
    from ada.config import logger

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collect(level)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(level)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


def _warnings(records) -> list[str]:
    return [r.getMessage() for r in records if r.levelno == logging.WARNING]


def test_a_finding_outside_any_collector_still_logs():
    """The library user who never opens a collector must lose nothing."""
    with _ada_logs() as records:
        current().omitted("sesam writer", "*TIE", "Constraint-2", "no Sesam representation")

    assert [m for m in _warnings(records) if "*TIE" in m], "an omission outside a collector was not logged"


def test_current_outside_a_collector_accumulates_nothing_globally():
    """Outside a collector each finding is logged and forgotten.

    A module-level report would grow for the life of the process -- a real leak in the REST worker,
    which converts many models -- so the throwaway must genuinely be thrown away.
    """
    current().omitted("sesam writer", "*MPC", "c1", "unsupported")

    assert current().findings == [], "a finding raised outside a collector was retained somewhere"
    assert current() is not current(), "current() handed out a shared report outside a collector"


def test_collect_captures_what_was_reported_inside_it():
    with collect() as report:
        current().omitted("abaqus reader", "*CLOAD", "", "not read by the Abaqus reader", count=2)
        current().approximated("sesam writer", "*TIE", "t1", "nearest-node rigid arm", max_distance=0.5)
        current().note("abaqus reader", "keywords", "", "inventory", count=1)

    kinds = [f.kind for f in report.findings]
    assert kinds == [OMITTED, APPROXIMATED, NOTE]
    assert report.counts() == {OMITTED: 2, APPROXIMATED: 1, NOTE: 1}


def test_repeats_of_the_same_finding_are_counted_and_logged_once():
    """The anti-flood rule: one line per construct kind, with a count — never one line per node."""
    with _ada_logs() as records:
        with collect() as report:
            for name in ("e1", "e2", "e3"):
                current().omitted("sesam writer", "CONNECTOR", name, "no Sesam representation")

    assert len(report.findings) == 1
    assert report.findings[0].count == 3
    assert len(_warnings(records)) == 1, "a repeated finding logged more than once"


def test_a_deduplicated_finding_keeps_the_first_subject_and_remembers_more():
    with collect() as report:
        for i in range(25):
            current().omitted("sesam writer", "SPRING", f"s{i}", "no Sesam representation")

    finding = report.findings[0]
    assert finding.count == 25
    assert finding.subject == "s0"
    # Capped: the count is exact, the named examples are a handful.
    assert len(finding.other_subjects) == conversion_report.MAX_OTHER_SUBJECTS
    assert finding.other_subjects[0] == "s1"


def test_findings_differing_only_by_reason_stay_separate():
    with collect() as report:
        current().omitted("sesam writer", "*TIE", "t1", "no master nodes")
        current().omitted("sesam writer", "*TIE", "t2", "no slave nodes")

    assert len(report.findings) == 2


def test_many_distinct_findings_are_not_swallowed_by_the_duplicate_log_filter():
    """``DuplicateFilter`` compares the *unformatted* ``record.msg``, so a lazily formatted
    message would collapse hundreds of different omissions into one line. This is the defect that
    once turned one bad regex into a 1.1-million-line stderr dump, in the other direction."""
    from ada.config import DuplicateFilter

    with _ada_logs() as records:
        with collect():
            for i in range(20):
                current().omitted("abaqus reader", f"*KEYWORD{i}", "", f"reason {i}")

    messages = _warnings(records)
    assert len(messages) == 20
    assert len({m for m in messages}) == 20

    # And the filter itself would have passed every one of them.
    dedupe = DuplicateFilter()
    passed = [r for r in records if r.levelno == logging.WARNING and dedupe.filter(r)]
    assert len(passed) == 20


def test_a_note_logs_at_info_so_it_does_not_read_as_a_problem():
    with _ada_logs() as records:
        with collect():
            current().note("abaqus reader", "keywords", "", "inventory", total=42)

    assert _warnings(records) == []
    assert [r for r in records if r.levelno == logging.INFO]


def test_status_reports_the_worst_kind_present():
    assert ConversionReport().status == STATUS_COMPLETED

    with collect() as approx:
        current().approximated("sesam writer", "*TIE", "t", "nearest node")
    assert approx.status == STATUS_WITH_APPROXIMATIONS
    assert approx.has_omissions is False

    with collect() as omitted:
        current().approximated("sesam writer", "*TIE", "t", "nearest node")
        current().omitted("sesam writer", "*MPC", "m", "unsupported")
    assert omitted.status == STATUS_WITH_OMISSIONS
    assert omitted.has_omissions is True


def test_collectors_nest_and_the_outer_one_is_restored():
    with collect() as outer:
        current().omitted("a", "X", "x", "r")
        with collect() as inner:
            current().omitted("b", "Y", "y", "r")
        current().omitted("c", "Z", "z", "r")

    assert [f.keyword for f in outer.findings] == ["X", "Z"]
    assert [f.keyword for f in inner.findings] == ["Y"]


def test_the_collector_is_restored_even_when_the_block_raises():
    """A conversion that dies partway through has still learnt something worth reporting."""
    with pytest.raises(RuntimeError):
        with collect() as report:
            current().omitted("sesam writer", "*TIE", "t", "unsupported")
            raise RuntimeError("the writer blew up")

    assert len(report.findings) == 1

    # And the context variable was reset, so the dead report is no longer what `current()`
    # returns. Asserting that a *new* collector is empty would prove nothing -- it sets its own
    # token regardless -- so check the leak directly: a finding raised outside any collector must
    # not land in the report that already ended.
    current().omitted("later", "AFTERWARDS", "x", "raised outside any collector")
    assert [f.keyword for f in report.findings] == ["*TIE"], "the ended report is still collecting"


def test_summary_puts_omissions_before_approximations_and_names_the_status():
    with collect() as report:
        current().approximated("sesam writer", "*TIE", "t1", "nearest-node rigid arm")
        current().omitted("abaqus reader", "*CLOAD", "", "not read", count=2)

    summary = report.summary()
    lines = summary.splitlines()
    assert lines[0].startswith(STATUS_WITH_OMISSIONS)
    assert "*CLOAD" in lines[1] and "*TIE" in lines[2]


def test_summary_of_a_clean_conversion_says_so_in_one_line():
    assert ConversionReport().summary().splitlines() == [f"{STATUS_COMPLETED}: nothing was omitted or approximated."]


def test_numpy_details_survive_json_serialisation():
    """Distances and node ids are measured with numpy, so they arrive as numpy types. A report
    that raised ``TypeError`` at write time — with the deck already on disk — would be worse than
    no report."""
    with collect() as report:
        current().approximated(
            "sesam writer",
            "*TIE",
            "t1",
            "nearest-node rigid arm",
            max_distance=np.float64(0.25),
            n_matched=np.int64(4),
            node_ids=np.array([5, 6, 7]),
            nested={"p95": np.float32(0.1)},
        )

    text = json.dumps(report.to_dict(input="a.inp"))
    payload = json.loads(text)
    details = payload["findings"][0]["details"]
    assert details["max_distance"] == pytest.approx(0.25)
    assert details["n_matched"] == 4
    assert details["node_ids"] == [5, 6, 7]
    assert details["nested"]["p95"] == pytest.approx(0.1, rel=1e-5)
    assert payload["input"] == "a.inp"
    assert payload["status"] == STATUS_WITH_APPROXIMATIONS
    assert payload["ada_version"]


def test_write_json_creates_the_file_and_its_parent(tmp_path):
    with collect() as report:
        current().omitted("sesam writer", "*MPC", "m1", "unsupported")

    path = tmp_path / "sub" / "r.json"
    written = report.write_json(path, output="out.FEM")

    assert written == path
    payload = json.loads(path.read_text())
    assert payload["findings"][0]["keyword"] == "*MPC"
    assert payload["output"] == "out.FEM"


def test_details_appear_in_the_logged_line_so_the_measure_is_visible():
    """An approximation nobody can size is indistinguishable from a guess."""
    with _ada_logs() as records:
        with collect():
            current().approximated("sesam writer", "*TIE", "t1", "nearest-node rigid arm", max_distance=0.125)

    assert any("max_distance=0.125" in m for m in _warnings(records))


def test_a_suspect_finding_is_faithful_output_over_doubtful_input():
    """Neither omitted nor approximated: nothing was lost and nothing was rounded, but the deck
    itself is probably wrong. Sesam sums linear dependencies, so two constraints making one node's
    DOF dependent silently give ``u_s = u_m1 + u_m2`` -- valid Sesam, not what anyone meant."""
    with _ada_logs() as records:
        with collect() as report:
            current().suspect("sesam writer", "BLDEP", "node 5 dof 1", "claimed by two constraints")

    assert report.status == STATUS_WITH_SUSPECTS
    assert report.has_suspects is True
    assert report.has_omissions is False
    assert report.needs_a_decision is True
    assert [m for m in _warnings(records) if "[SUSPECT]" in m], "a suspect finding must warn, not inform"


def test_suspect_ranks_below_an_omission_but_above_an_approximation():
    with collect() as report:
        current().approximated("sesam writer", "*TIE", "t", "nearest node")
        current().suspect("sesam writer", "BLDEP", "n5", "claimed twice")

    assert report.status == STATUS_WITH_SUSPECTS
    lines = report.summary().splitlines()
    assert "BLDEP" in lines[1] and "*TIE" in lines[2]

    with collect() as worse:
        current().suspect("sesam writer", "BLDEP", "n5", "claimed twice")
        current().omitted("sesam writer", "*MPC", "m", "unsupported")
    assert worse.status == STATUS_WITH_OMISSIONS


def test_a_note_alone_needs_no_decision_but_a_suspect_does():
    """The report file and ``--strict`` both hang off this distinction."""
    with collect() as only_note:
        current().note("abaqus reader", "keywords", "", "inventory")
    assert only_note.needs_a_decision is False

    with collect() as suspect:
        current().suspect("sesam writer", "BLDEP", "n5", "claimed twice")
    assert suspect.needs_a_decision is True
