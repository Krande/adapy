"""The audit window admits a job by when it was active, not only by when it was submitted.

A job that waited in the queue longer than the selected window vanished from the Audit tab the
moment it started running: the lower bound compared only ``ts`` (submission). The bound now also
admits rows that started inside the window and rows that are still queued or running.
"""

from __future__ import annotations

import datetime

from ada.comms.rest.db.audit_queries import _audit_predicates


def test_since_admits_started_and_active_rows():
    since = datetime.datetime(2026, 9, 11, tzinfo=datetime.timezone.utc)
    where, args = _audit_predicates(since=since)
    assert args == [since]
    assert len(where) == 1
    clause = where[0]
    assert "ts >= $1" in clause
    assert "started_at >= $1" in clause
    assert "status IN ('queued', 'running')" in clause
    assert clause.startswith("(") and clause.endswith(")")


def test_until_and_since_number_their_placeholders_in_order():
    since = datetime.datetime(2026, 9, 11, tzinfo=datetime.timezone.utc)
    until = datetime.datetime(2026, 9, 12, tzinfo=datetime.timezone.utc)
    where, args = _audit_predicates(user_sub="u1", since=since, until=until)
    assert args == ["u1", since, until]
    assert where[0] == "user_sub = $1"
    assert "$2" in where[1] and "ts <= $3" == where[2]


def test_no_window_means_no_bound():
    where, args = _audit_predicates()
    assert where == [] and args == []
