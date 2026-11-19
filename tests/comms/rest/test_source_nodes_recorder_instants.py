"""The database change-feed recorder takes ISO instants as the REST one does.

A writer is handed one or the other (``_source_nodes_recorder``) and cannot tell which. The REST
recorder sends strings on as JSON; the database one hands them to asyncpg, which refuses a string
for a timestamp column -- so a writer producing ISO strings (``ada.assets.ifc.sweep``) worked over
REST and failed against a database.
"""

from __future__ import annotations

import datetime

import pytest

from ada.comms.rest.worker.source_nodes import _with_aware_instant


def test_an_iso_string_becomes_an_aware_datetime():
    row = _with_aware_instant({"node_ref": "n1", "last_changed_at": "2026-01-02T03:04:05+00:00"})
    assert row["last_changed_at"] == datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)


def test_a_z_suffix_reads_as_utc():
    row = _with_aware_instant({"node_ref": "n1", "last_changed_at": "2026-01-02T03:04:05Z"})
    assert row["last_changed_at"].utcoffset() == datetime.timedelta(0)


def test_an_aware_datetime_passes_through_unchanged():
    when = datetime.datetime(2026, 1, 2, tzinfo=datetime.timezone.utc)
    node = {"node_ref": "n1", "last_changed_at": when, "action": "added"}
    assert _with_aware_instant(node) is node


@pytest.mark.parametrize("naive", ["2026-01-02T03:04:05", datetime.datetime(2026, 1, 2, 3, 4, 5)])
def test_an_instant_without_a_timezone_is_refused(naive):
    with pytest.raises(ValueError, match="no timezone"):
        _with_aware_instant({"node_ref": "n1", "last_changed_at": naive})


def test_a_string_that_is_no_instant_is_refused():
    with pytest.raises(ValueError, match="not an ISO instant"):
        _with_aware_instant({"node_ref": "n1", "last_changed_at": "yesterday"})
