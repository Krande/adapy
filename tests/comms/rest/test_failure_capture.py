"""Capture of a failed job's input into the admin-only failure corpus.

Pins the two properties that make it safe to run on every failure — one object
per distinct input, and it never raises — plus the eligibility rules.

Storage is faked: what matters is which calls are made, not that an object store
round-trips them.
"""

from __future__ import annotations

import asyncio

import pytest

from ada.comms.rest import failure_capture
from ada.comms.rest.scope import Scope

USER = Scope.user("sub-abc")
OTHER_USER = Scope.user("sub-xyz")
DERIVED = "_derived/m.ifc.glb"


class FakeStorage:
    def __init__(self, objects=None):
        self.objects = objects or {}  # (scope_prefix, key) -> head dict
        self.copies: list[tuple[str, str, str, str]] = []
        self.copy_error: Exception | None = None
        self.copy_delay = 0.0

    async def head(self, scope, key):
        return self.objects.get((scope.prefix(), key))

    async def exists(self, scope, key):
        return (scope.prefix(), key) in self.objects

    async def copy(self, src_scope, src_key, dst_scope, dst_key, *, overwrite=False):
        if self.copy_delay:
            await asyncio.sleep(self.copy_delay)
        if self.copy_error is not None:
            raise self.copy_error
        self.copies.append((src_scope.prefix(), src_key, dst_scope.prefix(), dst_key))
        self.objects[(dst_scope.prefix(), dst_key)] = {"e_tag": "copied", "size": 1}


class FakeDb:
    def __init__(self, corpus=None):
        self.corpus = corpus
        self.created: list[str] = []
        self.rows: dict[str, dict] = {}

    async def get_corpus_by_slug(self, pool, slug):
        return self.corpus

    async def create_corpus(self, pool, *, slug, name, description=None, created_by=None):
        self.created.append(slug)
        self.corpus = {"slug": slug}
        return self.corpus

    async def get_audit_by_job(self, pool, job_id):
        return self.rows.get(job_id)


def _head(etag="etag-1", size=1234, last_modified="2026-01-01T00:00:00+00:00"):
    return {"e_tag": etag, "size": size, "last_modified": last_modified}


def _run(storage, db=None, **kw):
    return asyncio.run(failure_capture.capture(storage, object(), db or FakeDb(), **kw))


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(failure_capture.ENABLED_ENV, "true")
    failure_capture._ensured_slugs.clear()


# ── the governance flag ───────────────────────────────────────────────────


def test_capture_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv(failure_capture.ENABLED_ENV, raising=False)
    storage = FakeStorage({(USER.prefix(), "m.ifc"): _head()})
    assert _run(storage, scope=USER, key="m.ifc") is None
    assert storage.copies == [], "must not duplicate user data until an operator opts in"


# ── eligibility ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "scope", [Scope.user("sub-abc"), Scope.shared(), Scope.project("proj-1")], ids=["user", "shared", "project"]
)
def test_live_scopes_are_captured(on, scope):
    storage = FakeStorage({(scope.prefix(), "m.ifc"): _head()})
    assert _run(storage, scope=scope, key="m.ifc") is not None
    assert len(storage.copies) == 1


def test_corpus_sources_are_not_captured(on):
    """Corpus assets are frozen, so a row naming one stays reproducible."""
    corpus = Scope.corpus("cad-baseline")
    storage = FakeStorage({(corpus.prefix(), "m.ifc"): _head()})
    assert _run(storage, scope=corpus, key="m.ifc") is None
    assert storage.copies == []


@pytest.mark.parametrize("action", [None, "convert"])
def test_derived_output_of_a_conversion_is_not_captured(on, action):
    """Rebuildable from the source the same row already points at."""
    storage = FakeStorage({(USER.prefix(), DERIVED): _head()})
    assert _run(storage, scope=USER, key=DERIVED, action=action) is None
    assert storage.copies == []


@pytest.mark.parametrize("action", ["view", "render"])
def test_a_failed_render_captures_the_derived_blob_it_read(on, action):
    """A render's input IS the derived artifact: re-deriving may not reproduce
    the bytes that actually failed, so the blob itself is the evidence."""
    storage = FakeStorage({(USER.prefix(), DERIVED): _head()})
    got = _run(storage, scope=USER, key=DERIVED, action=action)
    assert got is not None and got.endswith(".glb")
    assert len(storage.copies) == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [("error", True), ("failed", True), ("ok", False), ("done", False), ("cancelled", False), (None, False)],
)
def test_only_failing_statuses_count(status, expected):
    assert failure_capture.is_failure(status) is expected


# ── stored once ───────────────────────────────────────────────────────────


def test_identical_content_from_different_users_shares_one_object(on):
    same = _head(etag="shared-etag", size=999)
    storage = FakeStorage({(USER.prefix(), "a.ifc"): same, (OTHER_USER.prefix(), "b.ifc"): dict(same)})
    db = FakeDb()
    first = _run(storage, db, scope=USER, key="a.ifc")
    second = _run(storage, db, scope=OTHER_USER, key="b.ifc")
    assert first == second and first is not None
    assert len(storage.copies) == 1, "the second capture must not re-copy identical bytes"


def test_repeat_failure_on_one_object_copies_once(on):
    storage = FakeStorage({(USER.prefix(), "m.ifc"): _head()})
    db = FakeDb()
    keys = [_run(storage, db, scope=USER, key="m.ifc") for _ in range(5)]
    assert len(set(keys)) == 1
    assert len(storage.copies) == 1


def test_different_content_gets_different_objects(on):
    storage = FakeStorage(
        {(USER.prefix(), "a.ifc"): _head(etag="etag-a", size=1), (USER.prefix(), "b.ifc"): _head(etag="etag-b", size=2)}
    )
    db = FakeDb()
    assert _run(storage, db, scope=USER, key="a.ifc") != _run(storage, db, scope=USER, key="b.ifc")
    assert len(storage.copies) == 2


def test_same_bytes_under_different_names_share_one_object(on):
    """Dedup survives a rename — the filename is not part of the identity."""
    same = _head(etag="one-etag", size=42)
    storage = FakeStorage({(USER.prefix(), "a.ifc"): same, (USER.prefix(), "renamed.ifc"): dict(same)})
    db = FakeDb()
    assert _run(storage, db, scope=USER, key="a.ifc") == _run(storage, db, scope=USER, key="renamed.ifc")
    assert len(storage.copies) == 1


def test_same_bytes_offered_as_two_formats_stay_separate(on):
    same = _head(etag="one-etag", size=42)
    storage = FakeStorage({(USER.prefix(), "m.ifc"): same, (USER.prefix(), "m.step"): dict(same)})
    db = FakeDb()
    a, b = _run(storage, db, scope=USER, key="m.ifc"), _run(storage, db, scope=USER, key="m.step")
    assert a != b and a.endswith(".ifc") and b.endswith(".step")


def test_destination_keeps_the_extension_but_not_the_name(on):
    storage = FakeStorage({(USER.prefix(), "decks/big model.ifc"): _head()})
    got = _run(storage, scope=USER, key="decks/big model.ifc")
    assert got.endswith(".ifc") and "big model" not in got


def test_without_an_etag_dedup_falls_back_to_object_identity(on):
    """No content signal short of downloading, so repeat failures of one object
    still collapse but two identical files do not."""
    no_etag = {"e_tag": None, "size": 5, "last_modified": "2026-01-01T00:00:00+00:00"}
    storage = FakeStorage({(USER.prefix(), "a.ifc"): no_etag, (OTHER_USER.prefix(), "b.ifc"): dict(no_etag)})
    db = FakeDb()
    a = _run(storage, db, scope=USER, key="a.ifc")
    assert a == _run(storage, db, scope=USER, key="a.ifc")
    assert a != _run(storage, db, scope=OTHER_USER, key="b.ifc")


# ── never raises ──────────────────────────────────────────────────────────


def test_an_input_that_is_already_gone_is_not_an_error(on):
    storage = FakeStorage({})
    assert _run(storage, scope=USER, key="gone.ifc") is None
    assert storage.copies == []


def test_a_failed_copy_never_propagates(on):
    """Recording the failure matters more than preserving its input."""
    storage = FakeStorage({(USER.prefix(), "m.ifc"): _head()})
    storage.copy_error = RuntimeError("object store unavailable")
    assert _run(storage, scope=USER, key="m.ifc") is None


def test_a_slow_copy_is_bounded(on, monkeypatch):
    monkeypatch.setenv(failure_capture.TIMEOUT_ENV, "0.05")
    storage = FakeStorage({(USER.prefix(), "m.ifc"): _head()})
    storage.copy_delay = 5.0
    assert _run(storage, scope=USER, key="m.ifc") is None


def test_corpus_row_is_created_so_the_scope_is_reachable_in_the_ui(on):
    storage = FakeStorage({(USER.prefix(), "m.ifc"): _head()})
    db = FakeDb(corpus=None)
    _run(storage, db, scope=USER, key="m.ifc")
    assert db.created == [failure_capture.slug()]


# ── job-id resolution (the worker's single hook) ──────────────────────────


def test_capture_for_job_resolves_scope_and_action_from_the_row(on):
    storage = FakeStorage({(USER.prefix(), DERIVED): _head()})
    db = FakeDb()
    db.rows["job-1"] = {"scope_kind": "user", "scope_id": "sub-abc", "key": DERIVED, "action": "render"}
    assert asyncio.run(failure_capture.capture_for_job(storage, object(), db, "job-1")) is not None
    assert storage.copies[0][:2] == (USER.prefix(), DERIVED)


def test_capture_for_job_ignores_a_corpus_row(on):
    storage = FakeStorage({(Scope.corpus("base").prefix(), "m.ifc"): _head()})
    db = FakeDb()
    db.rows["job-2"] = {"scope_kind": "corpus", "scope_id": "base", "key": "m.ifc", "action": "convert"}
    assert asyncio.run(failure_capture.capture_for_job(storage, object(), db, "job-2")) is None
    assert storage.copies == []


def test_capture_for_job_tolerates_a_missing_row(on):
    assert asyncio.run(failure_capture.capture_for_job(FakeStorage({}), object(), FakeDb(), "nope")) is None
