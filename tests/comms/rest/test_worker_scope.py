"""Regression tests for worker._scope_of — the job→Scope reconstruction.

A corpus job previously fell through to Scope.shared(), so the worker fetched
the source from shared/<key> and every corpus-scope conversion 404'd.
"""

from __future__ import annotations

import pytest

from ada.comms.rest.queue import Job
from ada.comms.rest.worker import _scope_of


def _job(scope_kind: str, scope_id: str | None) -> Job:
    return Job(
        job_id="j",
        source_key="src.ifc",
        derived_key="d",
        status="queued",
        target_format="glb",
        progress=0.0,
        stage="queued",
        created_at=0.0,
        updated_at=0.0,
        scope_kind=scope_kind,
        scope_id=scope_id,
    )


@pytest.mark.parametrize(
    "kind,sid,exp_kind,exp_prefix",
    [
        ("corpus", "basic", "corpus", "corpus/basic"),
        ("user", "sub-7", "user", "users/sub-7"),
        ("project", "p1", "project", "projects/p1"),
        ("shared", None, "shared", "shared"),
    ],
)
def test_scope_of_maps_each_kind(kind, sid, exp_kind, exp_prefix):
    s = _scope_of(_job(kind, sid))
    assert s.kind == exp_kind
    assert s.prefix() == exp_prefix


def test_scope_of_corpus_is_not_shared():
    # The specific regression: corpus must not silently degrade to shared.
    assert _scope_of(_job("corpus", "basic")).prefix() == "corpus/basic"
