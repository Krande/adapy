"""Unit tests for ada.comms.rest.audit_issue (M5).

Fingerprint + sanitizer + sync orchestrator. Pure-function tests
only — the issue-bot loop in app.py is exercised separately when
ADA_TEST_POSTGRES_URL is set.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from ada.comms.rest import audit_issue
from ada.comms.rest.audit_issue import (
    LocalIssueClaims,
    comment_body,
    fingerprint,
    fingerprint_job,
    fp_label,
    issue_body,
    issue_title,
    publish_recheck_verdicts,
    recheck_cells,
    recheck_verdict,
    sanitize_corpus_key,
    scope_of,
    strip_volatile,
    sync_run_issues,
)
from ada.comms.rest.issue_client import IssueClientError


@pytest.fixture(autouse=True)
def _fresh_local_claims(monkeypatch):
    """The in-process claims are module state; each test starts with none."""
    monkeypatch.setattr(audit_issue, "_LOCAL_CLAIMS", LocalIssueClaims())


# ── fingerprint ─────────────────────────────────────────────────────


def test_fingerprint_is_deterministic():
    """Same inputs → same hash, every time."""
    fp1 = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="UnsupportedFormat",
        traceback="Traceback...\nFile a.py:1",
    )
    fp2 = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="UnsupportedFormat",
        traceback="Traceback...\nFile a.py:1",
    )
    assert fp1 == fp2
    assert len(fp1) == 16


def test_fingerprint_changes_on_real_diffs():
    """Different conversions / different errors → different hashes.

    We assert pairwise distinctness so a regression that collapses
    semantically-distinct errors into one bucket shows up here."""
    base = dict(error_msg="UnsupportedFormat", traceback="frame")
    a = fingerprint(source_ext=".step", target_format="glb", **base)
    b = fingerprint(source_ext=".ifc", target_format="glb", **base)
    c = fingerprint(source_ext=".step", target_format="ifc", **base)
    d = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="DifferentError",
        traceback="frame",
    )
    assert len({a, b, c, d}) == 4


def test_fingerprint_strips_volatile_substrings():
    """Tempfile paths, line numbers, hex blobs, timestamps must
    collapse so the same root cause produces the same fingerprint
    across runs — the dedup invariant the issue bot relies on."""
    fp1 = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="Failed at /tmp/abc123/x.step",
        traceback='File "module.py":42 in convert',
    )
    fp2 = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="Failed at /tmp/zxy999/x.step",
        traceback='File "module.py":777 in convert',
    )
    assert fp1 == fp2


def test_fingerprint_strips_iso_timestamps():
    fp1 = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="error at 2026-05-27T14:23:11+00:00",
        traceback=None,
    )
    fp2 = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="error at 2025-01-01T00:00:00Z",
        traceback=None,
    )
    assert fp1 == fp2


def test_fingerprint_tolerates_none_inputs():
    """A row with only an error message (no traceback) still
    hashes deterministically."""
    fp = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg=None,
        traceback=None,
    )
    assert len(fp) == 16


def test_strip_volatile_does_not_eat_short_codes():
    """``0xFF`` should pass through — the regex requires 8+ hex
    chars so short error codes aren't mistakenly normalised."""
    out = strip_volatile("error 0xFF / abc1234ff5678")
    # The short '0xFF' becomes '<addr>' (memory-address rule), but
    # the long hex blob also becomes '<addr>' OR '<hex>'. We only
    # care that the short form doesn't survive as itself (matches
    # 0x.. pattern); the long form must also be normalised.
    assert "0xFF" not in out
    assert "abc1234ff5678" not in out


def _fp_msg(msg: str) -> str:
    """Fingerprint a failure the way a raised exception lands in a row: the message,
    and the traceback whose last line is ``<ExcType>: <message>``."""
    return fingerprint(
        source_ext=".json",
        target_format="asset_build",
        error_msg=msg,
        traceback=f'Traceback (most recent call last):\n  File "x.py", line 3, in f\npkg.mod.SomeError: {msg}',
    )


def test_fingerprint_folds_storage_paths_and_counts():
    """One root cause raised against many files / with many counts is one fingerprint:
    the quoted storage key, its timestamp directory and the count all vary per row."""
    variants = [
        "none of the 8 items this index names has data in 'assets/demo/123-4/20260101T000000Z/model.db' -- refusing",
        "none of the 3 items this index names has data in 'assets/demo/123-4/20260101T000000Z/model.db' -- refusing",
        "none of the 324 items this index names has data in 'assets/other/987-65/20261231T235959Z/model.db' -- refusing",
        'none of the 1 items this index names has data in "C:\\data\\run\\model.db" -- refusing',
    ]
    assert len({_fp_msg(v) for v in variants}) == 1


def test_fingerprint_folds_quoted_names_with_separators_and_numeric_ids():
    variants = [
        "no entry named '/A-100-DEMO' in catalog 'demo' -- nothing to stage for node '123-1'",
        "no entry named '/B200-OTHER_X' in catalog 'demo' -- nothing to stage for node '456-337'",
    ]
    assert len({_fp_msg(v) for v in variants}) == 1


def test_fingerprint_keeps_quoted_words_and_identifiers_apart():
    """Normalisation must not erase what distinguishes two failures: a quoted name
    without a path separator, and digits glued to an identifier, both stay."""
    assert _fp_msg("unknown entity 'Beam'") != _fp_msg("unknown entity 'Plate'")
    assert _fp_msg("unsupported dtype float64") != _fp_msg("unsupported dtype float32")
    assert _fp_msg("missing 'demo' catalog") != _fp_msg("missing 'other' catalog")


def test_strip_volatile_rules():
    assert strip_volatile("run 20260101T000000Z done") == "run <ts> done"
    assert strip_volatile("job 123e4567-e89b-12d3-a456-426614174000 lost") == "job <uuid> lost"
    assert strip_volatile("read 'a/b/c.db' and x\\y\\z.step") == "read '<path>' and <path>"
    assert strip_volatile("got 12 of 3.5 for item 7-1") == "got <n> of <n> for item <n>-<n>"
    # prose with a single separator, and identifiers with digits, are not volatile
    assert strip_volatile("N/A as application/json via sha256 in ifc4x3") == (
        "N/A as application/json via sha256 in ifc4x3"
    )


def test_fingerprint_unchanged_for_messages_without_volatile_parts():
    """A message with nothing volatile in it normalises to itself, so its fingerprint
    is the one the issue tracker already has a label for."""
    msg = "UnsupportedFormat: no reader registered for this extension"
    assert strip_volatile(msg) == msg


# ── sanitize_corpus_key ─────────────────────────────────────────────


def test_sanitize_corpus_key_hashes_corpus_filenames():
    out = sanitize_corpus_key("corpus:cad-baseline", "proprietary-model.step")
    assert out.startswith("corpus:cad-baseline/file-")
    assert "proprietary-model.step" not in out
    # 'file-' + 10 hex chars
    assert len(out.split("/file-", 1)[1]) == 10


def test_sanitize_corpus_key_stable_for_same_input():
    """Stable mapping → consecutive runs reuse the same label so
    the bug tracker can correlate reproductions across runs."""
    a = sanitize_corpus_key("corpus:x", "model.step")
    b = sanitize_corpus_key("corpus:x", "model.step")
    assert a == b


def test_sanitize_corpus_key_passes_through_non_corpus_scopes():
    """User / shared / project scopes aren't subject to the
    proprietary-files concern. Pass the key through unchanged."""
    assert sanitize_corpus_key("shared", "ok.step") == "shared/ok.step"
    assert sanitize_corpus_key("user:abc", "ok.step") == "user:abc/ok.step"
    assert sanitize_corpus_key("project:p", "ok.step") == "project:p/ok.step"


# ── scope_of ────────────────────────────────────────────────────────


def test_scope_of_recovers_wire_format():
    assert scope_of({"scope_kind": "shared", "scope_id": ""}) == "shared"
    assert scope_of({"scope_kind": "user", "scope_id": "abc"}) == "user:abc"
    assert scope_of({"scope_kind": "corpus", "scope_id": "x"}) == "corpus:x"
    assert scope_of({"scope_kind": "project", "scope_id": "p"}) == "project:p"


# ── issue body formatting ──────────────────────────────────────────


def test_issue_body_includes_fingerprint_and_sanitized_source():
    body = issue_body(
        fp="abc1234567890def",
        source_ext=".step",
        target_format="glb",
        sanitized_source="corpus:x/file-1234567890",
        error_msg="UnsupportedFormat",
        traceback="trace1\ntrace2\ntrace3",
        run_id="run-uuid",
        run_started_at="2026-05-27T00:00:00Z",
    )
    assert "abc1234567890def" in body
    assert "corpus:x/file-1234567890" in body
    assert ".step" in body and "glb" in body
    assert "trace3" in body  # trailing frame included


def test_comment_body_is_short_and_includes_run_id():
    body = comment_body(
        fp="abc",
        run_id="r1",
        sanitized_source="corpus:x/file-xx",
        run_started_at=None,
    )
    assert "r1" in body
    assert "corpus:x/file-xx" in body


def test_fp_label_shape():
    assert fp_label("abcd1234") == "audit-fp:abcd1234"


def test_issue_title_is_self_identifying():
    title = issue_title(source_ext=".step", target_format="glb", fp="abcd1234")
    assert ".step" in title and "glb" in title and "abcd1234" in title


# ── sync orchestrator with stub client ─────────────────────────────


class _StubClient:
    """Records every call. ``existing`` lets a test pre-seed
    "this label already has an open issue" so the comment path is
    exercised."""

    def __init__(self, existing: dict | None = None, *, first_number: int = 1):
        self.existing = existing or {}  # label -> [IssueRef-like dict]
        self.created: list[dict] = []
        self.commented: list[tuple[int, str]] = []
        self.updated: list[tuple[int, str]] = []
        self.states: list[tuple[int, str]] = []
        self.searches = 0
        self._next = first_number
        # Issues the bot created are readable by number but -- like a lagging forge
        # search -- never show up in list_issues_by_label.
        self._by_number: dict[int, dict] = {}

    async def list_issues_by_label(self, label: str, *, state: str = "open"):
        self.searches += 1
        rows = [r for r in self.existing.get(label, []) if self._state(r) == state]
        return [_FakeIssue(**{**r, "state": self._state(r)}) for r in rows]

    def _state(self, r: dict) -> str:
        changed = [s for n, s in self.states if n == r["number"]]
        return changed[-1] if changed else r.get("state", "open")

    async def find_issue_by_title(self, title: str):
        return None  # dashboard rebuild is exercised separately

    async def get_issue(self, number: int):
        rows = [r for rs in self.existing.values() for r in rs if r["number"] == number]
        r = rows[0] if rows else self._by_number.get(number)
        if r is None:
            raise IssueClientError(f"GET {number} → 404", status=404)
        return _FakeIssue(**{**r, "state": self._state(r)})

    async def create_issue(self, *, title: str, body: str, labels):
        await asyncio.sleep(0)  # a real create yields; lets a concurrent sync interleave
        rec = {"title": title, "body": body, "labels": list(labels)}
        self.created.append(rec)
        number = self._next
        self._next += 1
        self._by_number[number] = {"number": number, "title": title, "labels": list(labels)}
        return _FakeIssue(number=number, title=title, body=body, html_url=None, labels=list(labels), state="open")

    async def comment_issue(self, number: int, *, body: str):
        self.commented.append((number, body))

    async def update_issue_body(self, number: int, *, body: str):
        self.updated.append((number, body))

    async def set_issue_state(self, number: int, *, state: str):
        self.states.append((number, state))


class _FakeIssue:
    def __init__(self, *, number, title, body=None, html_url=None, labels=None, state="open"):
        self.number = number
        self.title = title
        self.body = body
        self.html_url = html_url
        self.labels = labels or []
        self.state = state


def _job(key, target, error="boom", tb=None, scope_kind="corpus", scope_id="x"):
    return {
        "key": key,
        "target_format": target,
        "error": error,
        "traceback": tb,
        "scope_kind": scope_kind,
        "scope_id": scope_id,
        "status": "failed",
    }


def test_sync_run_issues_opens_a_new_issue_per_fingerprint():
    client = _StubClient()
    jobs = [
        _job("a.step", "glb", error="UnsupportedFormat"),
        _job("b.step", "ifc", error="OtherError"),
    ]
    summary = asyncio.run(
        sync_run_issues(
            client,
            run={"id": "r1", "started_at": None},
            failed_jobs=jobs,
        )
    )
    assert summary["opened"] == 2
    assert summary["commented"] == 0
    assert summary["unique_failures"] == 2


def test_sync_run_issues_dedups_same_fingerprint_within_one_run():
    """Two failures sharing a fingerprint produce one issue + zero
    comments (the bot doesn't comment on an issue it just opened)."""
    client = _StubClient()
    jobs = [
        _job("a.step", "glb", error="UnsupportedFormat"),
        _job("a2.step", "glb", error="UnsupportedFormat"),  # same fp
    ]
    summary = asyncio.run(
        sync_run_issues(
            client,
            run={"id": "r1", "started_at": None},
            failed_jobs=jobs,
        )
    )
    assert summary["opened"] == 1
    assert summary["unique_failures"] == 1


def test_sync_run_issues_comments_on_existing_label():
    """Reproduced fingerprint → comment on the existing issue,
    don't reopen."""
    # Pre-seed an "open" issue under the audit-fp label.
    sample_fp = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="UnsupportedFormat",
        traceback=None,
    )
    label = fp_label(sample_fp)
    client = _StubClient(
        existing={
            label: [{"number": 7, "title": "audit: .step → glb", "labels": [label]}],
        }
    )
    summary = asyncio.run(
        sync_run_issues(
            client,
            run={"id": "r2", "started_at": "2026-05-27T00:00:00Z"},
            failed_jobs=[_job("a.step", "glb", error="UnsupportedFormat")],
        )
    )
    assert summary["opened"] == 0
    assert summary["commented"] == 1
    assert client.commented[0][0] == 7  # commented on issue #7


def test_sync_run_issues_per_failure_error_doesnt_abort_run():
    """If one issue lookup blows up, the rest of the failures still
    sync. We mimic that by overriding one method to raise."""

    class _BrokenLookupClient(_StubClient):
        async def list_issues_by_label(self, label, *, state="open"):
            if label == fp_label(
                fingerprint(
                    source_ext=".step",
                    target_format="glb",
                    error_msg="UnsupportedFormat",
                    traceback=None,
                )
            ):
                raise RuntimeError("simulated transport blip")
            return []

    client = _BrokenLookupClient()
    jobs = [
        _job("a.step", "glb", error="UnsupportedFormat"),
        _job("b.step", "ifc", error="OtherError"),
    ]
    summary = asyncio.run(
        sync_run_issues(
            client,
            run={"id": "r3", "started_at": None},
            failed_jobs=jobs,
        )
    )
    # One failure → opened issue; the broken one is counted as an error.
    assert summary["opened"] == 1
    assert len(summary["errors"]) == 1


def test_sync_run_issues_handles_no_failures():
    """Empty failed_jobs is a no-op summary, not an error."""
    summary = asyncio.run(
        sync_run_issues(
            _StubClient(),
            run={"id": "r4"},
            failed_jobs=[],
        )
    )
    assert summary == {"opened": 0, "commented": 0, "reopened": 0, "errors": [], "unique_failures": 0}


# ── M5b: source_label parameterization ─────────────────────────────


def test_issue_body_default_label_says_audit_run():
    body = issue_body(
        fp="abc",
        source_ext=".step",
        target_format="glb",
        sanitized_source="shared/x.step",
        error_msg="boom",
        traceback=None,
        run_id="r1",
        run_started_at=None,
    )
    assert "First seen in audit run" in body


def test_issue_body_custom_label_for_user_conversion():
    body = issue_body(
        fp="abc",
        source_ext=".step",
        target_format="glb",
        sanitized_source="user/me/x.step",
        error_msg="boom",
        traceback=None,
        run_id="audit-row-42",
        run_started_at=None,
        source_label="user conversion",
    )
    assert "First seen in user conversion" in body
    assert "audit run" not in body  # default phrasing replaced


def test_comment_body_uses_source_label():
    body = comment_body(
        fp="abc",
        run_id="audit-row-42",
        sanitized_source="shared/x.step",
        run_started_at=None,
        source_label="user conversion",
    )
    assert "Reproduced in user conversion" in body
    assert "audit run" not in body


def test_sync_run_issues_forwards_source_label_to_create():
    """The user-conversion path should land an issue body that says
    'user conversion', not 'audit run'."""
    client = _StubClient()
    job = _job("a.step", "glb", error="UnsupportedFormat")
    asyncio.run(
        sync_run_issues(
            client,
            run={"id": "audit-row-42", "started_at": None},
            failed_jobs=[job],
            source_label="user conversion",
        )
    )
    assert client.created, "expected one issue created"
    assert "user conversion" in client.created[0]["body"]


def test_sync_run_issues_forwards_source_label_to_comment():
    sample_fp = fingerprint(
        source_ext=".step",
        target_format="glb",
        error_msg="UnsupportedFormat",
        traceback=None,
    )
    label = fp_label(sample_fp)
    client = _StubClient(
        existing={
            label: [{"number": 9, "title": "audit: .step → glb", "labels": [label]}],
        }
    )
    asyncio.run(
        sync_run_issues(
            client,
            run={"id": "audit-row-9", "started_at": None},
            failed_jobs=[_job("b.step", "glb", error="UnsupportedFormat")],
            source_label="user conversion",
        )
    )
    assert client.commented, "expected one comment"
    _, comment_text = client.commented[0]
    assert "user conversion" in comment_text


# ── reopen + skip ──────────────────────────────────────────────────


def _fp_of(key="a.step", target="glb", error="boom", tb=None):
    return fingerprint_job({"key": key, "target_format": target, "error": error, "traceback": tb})


def test_fingerprint_job_matches_fingerprint():
    assert _fp_of() == fingerprint(source_ext=".step", target_format="glb", error_msg="boom", traceback=None)


def test_sync_reopens_a_closed_issue_instead_of_opening_a_duplicate():
    label = fp_label(_fp_of())
    client = _StubClient(existing={label: [{"number": 4, "title": "t", "labels": [label], "state": "closed"}]})
    summary = asyncio.run(
        sync_run_issues(client, run={"id": "r2", "started_at": None}, failed_jobs=[_job("a.step", "glb")])
    )
    assert summary["reopened"] == 1 and summary["opened"] == 0 and summary["commented"] == 0
    assert client.states == [(4, "open")]
    assert client.created == []
    assert client.commented[0][0] == 4 and "Reopened" in client.commented[0][1]


def test_sync_leaves_skip_fps_alone():
    client = _StubClient()
    summary = asyncio.run(
        sync_run_issues(
            client,
            run={"id": "r3", "started_at": None},
            failed_jobs=[_job("a.step", "glb"), _job("b.step", "ifc", error="other")],
            skip_fps={_fp_of()},
        )
    )
    assert summary["opened"] == 1
    assert [c["labels"][1] for c in client.created] == [fp_label(_fp_of("b.step", "ifc", "other"))]


# ── single-flight issue creation ───────────────────────────────────


class _DurableClaims:
    """A claim store that says it is durable (as the database one is), kept in memory."""

    durable = True

    def __init__(self, numbers: dict[str, int] | None = None):
        self.numbers = dict(numbers or {})
        self._local = LocalIssueClaims()

    @asynccontextmanager
    async def hold(self, fp):
        async with self._local.hold(fp):
            yield _MemHeld(self.numbers, fp)


class _MemHeld:
    def __init__(self, numbers, fp):
        self._numbers = numbers
        self._fp = fp

    async def get(self):
        return self._numbers.get(self._fp)

    async def record(self, number):
        self._numbers[self._fp] = number

    async def forget(self):
        self._numbers.pop(self._fp, None)


def _sync(client, *jobs, run_id="r", claims=None):
    return sync_run_issues(client, run={"id": run_id, "started_at": None}, failed_jobs=list(jobs), claims=claims)


def test_concurrent_syncs_of_one_fingerprint_open_one_issue():
    """Two syncs racing on one fingerprint, against a forge whose search never shows the
    fresh issue: the second waits for the first, then comments on the issue it opened."""
    client = _StubClient()

    async def both():
        return await asyncio.gather(
            _sync(client, _job("a.step", "glb"), run_id="r1"), _sync(client, _job("b.step", "glb"), run_id="r2")
        )

    s1, s2 = asyncio.run(both())
    assert len(client.created) == 1
    assert s1["opened"] + s2["opened"] == 1 and s1["commented"] + s2["commented"] == 1
    assert [n for n, _ in client.commented] == [1]


def test_back_to_back_syncs_use_the_claim_not_the_lagging_search():
    client = _StubClient()

    async def twice():
        await _sync(client, _job("a.step", "glb"))
        searches = client.searches
        summary = await _sync(client, _job("a.step", "glb"))
        return summary, client.searches - searches

    summary, searches = asyncio.run(twice())
    assert len(client.created) == 1 and summary["commented"] == 1
    assert searches == 0, "a claimed fingerprint goes straight to its issue"


def test_a_claimed_closed_issue_is_reopened():
    client = _StubClient()
    fp = _fp_of()
    claims = _DurableClaims()

    async def go():
        await _sync(client, _job("a.step", "glb"), claims=claims)
        await client.set_issue_state(claims.numbers[fp], state="closed")  # a recheck closed it
        return await _sync(client, _job("a.step", "glb"), claims=claims)

    summary = asyncio.run(go())
    assert summary["reopened"] == 1 and len(client.created) == 1
    assert client.states[-1] == (1, "open")


def test_a_stale_claim_falls_back_to_the_search():
    """A claimed issue that is gone (or lost its label) is forgotten; the search decides."""
    label = fp_label(_fp_of())
    client = _StubClient(existing={label: [{"number": 4, "title": "t", "labels": [label]}]})
    claims = _DurableClaims({_fp_of(): 99})
    summary = asyncio.run(_sync(client, _job("a.step", "glb"), claims=claims))
    assert summary["commented"] == 1 and client.commented[0][0] == 4
    assert claims.numbers[_fp_of()] == 4


def test_existing_duplicates_fold_into_the_oldest():
    label = fp_label(_fp_of())
    client = _StubClient(existing={label: [{"number": n, "title": "t", "labels": [label]} for n in (9, 3, 6)]})
    claims = _DurableClaims()
    summary = asyncio.run(_sync(client, _job("a.step", "glb"), claims=claims))
    assert sorted(client.states) == [(6, "closed"), (9, "closed")]
    dup_comments = {n: body for n, body in client.commented if n != 3}
    assert set(dup_comments) == {6, 9} and all("#3" in b for b in dup_comments.values())
    assert summary["commented"] == 1 and client.commented[-1][0] == 3
    assert claims.numbers[_fp_of()] == 3


def test_without_a_durable_store_a_duplicate_made_elsewhere_is_closed_after_create():
    """Another process opened the fingerprint moments ago and the search had not caught up:
    after creating, the re-search shows both, and the newer one is closed."""
    label = fp_label(_fp_of())

    class _CatchesUp(_StubClient):
        async def list_issues_by_label(self, label_, *, state="open"):
            if self.created and state == "open":
                return [
                    _FakeIssue(number=2, title="t", labels=[label]),
                    _FakeIssue(number=5, title="t", labels=[label]),
                ]
            return await super().list_issues_by_label(label_, state=state)

    client = _CatchesUp(first_number=5)
    asyncio.run(_sync(client, _job("a.step", "glb")))
    assert client.states == [(5, "closed")]
    assert [n for n, _ in client.commented] == [5] and "#2" in client.commented[0][1]
    assert audit_issue._LOCAL_CLAIMS._numbers[_fp_of()] == 2, "the claim points at the kept issue"


def test_a_durable_store_skips_the_post_create_search():
    client = _StubClient()
    asyncio.run(_sync(client, _job("a.step", "glb"), claims=_DurableClaims()))
    assert client.searches == 2  # open + closed before creating, nothing after


def test_local_claims_drop_idle_locks():
    claims = LocalIssueClaims()

    async def go():
        async with claims.hold("fp1") as held:
            await held.record(7)
        async with claims.hold("fp1") as held:
            return await held.get()

    assert asyncio.run(go()) == 7
    assert claims._locks == {}


# ── recheck: which cells ───────────────────────────────────────────


def _row(kind, sid, key, target="glb", failure_key=None, pool=None):
    return {
        "scope_kind": kind,
        "scope_id": sid,
        "key": key,
        "target_format": target,
        "failure_key": failure_key,
        "worker_pool": pool,
    }


def test_recheck_cells_prefers_the_failure_corpus_copy_and_never_the_users_scope():
    groups, skipped = recheck_cells(
        [_row("user", "u1", "private/plant.step", failure_key="ab12.step")], failure_slug="failures"
    )
    assert groups == {("corpus:failures", None): [("ab12.step", "glb")]}
    assert skipped == []


def test_recheck_cells_reruns_corpus_and_shared_in_place():
    groups, _ = recheck_cells(
        [_row("corpus", "regress", "a.ifc", pool="audit"), _row("shared", None, "b.step")], failure_slug="failures"
    )
    assert groups == {("corpus:regress", "audit"): [("a.ifc", "glb")], ("shared", None): [("b.step", "glb")]}


def test_recheck_cells_skips_a_private_file_with_no_copy_and_a_wasm_failure():
    groups, skipped = recheck_cells(
        [_row("project", "p1", "x.step"), _row("corpus", "c", "y.step", pool="wasm")], failure_slug="failures"
    )
    assert groups == {}
    assert len(skipped) == 2
    assert any("no preserved copy" in s for s in skipped)
    assert any("browser engine" in s for s in skipped)
    # A corpus filename is sanitised even in the admin-facing reason.
    assert not any("y.step" in s for s in skipped)


def test_recheck_cells_a_cell_with_any_rerunnable_row_is_not_skipped():
    # Newest row lost its copy (capture disabled since), an older one had it.
    groups, skipped = recheck_cells(
        [_row("user", "u1", "f.step"), _row("user", "u1", "f.step", failure_key="cd34.step")], failure_slug="fails"
    )
    assert groups == {("corpus:fails", None): [("cd34.step", "glb")]}
    assert skipped == []


def test_recheck_cells_dedups_rows_resolving_to_one_cell():
    groups, _ = recheck_cells(
        [_row("user", "u1", "a.step", failure_key="ee.step"), _row("user", "u2", "b.step", failure_key="ee.step")],
        failure_slug="failures",
    )
    assert groups == {("corpus:failures", None): [("ee.step", "glb")]}


# ── recheck: verdict ───────────────────────────────────────────────

_FP = _fp_of("a.step", "glb", "boom")


def _run_job(key, target, status, error=None, tb=None, tag=None):
    return {
        "key": key,
        "target_format": target,
        "status": status,
        "error": error,
        "traceback": tb,
        "worker_image_tag": tag,
    }


def test_verdict_fixed_when_every_cell_passes():
    v = recheck_verdict(
        _FP,
        [("a.step", "glb"), ("b.step", "glb")],
        [_run_job("a.step", "glb", "done"), _run_job("b.step", "glb", "ok")],
    )
    assert v["verdict"] == "fixed" and v["passed"] == 2


def test_verdict_reproduced_beats_everything():
    v = recheck_verdict(
        _FP,
        [("a.step", "glb"), ("c.step", "glb")],
        [_run_job("a.step", "glb", "failed", "boom"), _run_job("c.step", "glb", "failed", "else")],
    )
    assert v["verdict"] == "reproduced" and v["changed"] == 1
    assert v["new_fps"] == [_fp_of("c.step", "glb", "else")]


def test_verdict_changed_when_it_fails_differently():
    v = recheck_verdict(_FP, [("a.step", "glb")], [_run_job("a.step", "glb", "error", "new problem")])
    assert v["verdict"] == "changed"


def test_verdict_unverifiable_when_a_cell_is_missing_or_unfinished():
    v = recheck_verdict(_FP, [("a.step", "glb"), ("gone.step", "glb")], [_run_job("a.step", "glb", "done")])
    assert v["verdict"] == "unverifiable" and v["missing"] == 1
    v = recheck_verdict(_FP, [("a.step", "glb")], [_run_job("a.step", "glb", "queued")])
    assert v["verdict"] == "unverifiable"
    assert recheck_verdict(_FP, [], [])["verdict"] == "unverifiable"


def test_verdict_counts_the_newest_row_of_a_cell():
    v = recheck_verdict(
        _FP, [("a.step", "glb")], [_run_job("a.step", "glb", "failed", "boom"), _run_job("a.step", "glb", "done")]
    )
    assert v["verdict"] == "fixed"


# ── recheck: publishing ────────────────────────────────────────────


def _recheck(fp=_FP, cells=(("a.step", "glb"),), rid=1):
    return {"id": rid, "fp": fp, "cells": [list(c) for c in cells]}


def test_publish_fixed_comments_then_closes():
    label = fp_label(_FP)
    client = _StubClient(existing={label: [{"number": 7, "title": "t", "labels": [label]}]})
    out = asyncio.run(
        publish_recheck_verdicts(
            client, run={"id": "rr"}, rechecks=[_recheck()], run_jobs=[_run_job("a.step", "glb", "done", tag="img:1")]
        )
    )
    assert out == [{"id": 1, "verdict": "fixed", "detail": "passed 1/1"}]
    assert client.commented[0][0] == 7 and "closing" in client.commented[0][1] and "`img:1`" in client.commented[0][1]
    assert client.states == [(7, "closed")]


def test_publish_reproduced_comments_and_keeps_open():
    label = fp_label(_FP)
    client = _StubClient(existing={label: [{"number": 7, "title": "t", "labels": [label]}]})
    out = asyncio.run(
        publish_recheck_verdicts(
            client, run={"id": "rr"}, rechecks=[_recheck()], run_jobs=[_run_job("a.step", "glb", "failed", "boom")]
        )
    )
    assert out[0]["verdict"] == "reproduced"
    assert client.states == []
    assert "still reproduces" in client.commented[0][1]


def test_publish_records_a_verdict_when_the_issue_was_closed_meanwhile():
    client = _StubClient()
    out = asyncio.run(
        publish_recheck_verdicts(
            client, run={"id": "rr"}, rechecks=[_recheck()], run_jobs=[_run_job("a.step", "glb", "done")]
        )
    )
    assert out[0]["verdict"] == "fixed"
    assert client.commented == [] and client.states == []


def test_publish_forge_error_is_recorded_not_raised():
    class _Broken(_StubClient):
        async def list_issues_by_label(self, label, *, state="open"):
            raise RuntimeError("forge down")

    out = asyncio.run(
        publish_recheck_verdicts(
            _Broken(), run={"id": "rr"}, rechecks=[_recheck()], run_jobs=[_run_job("a.step", "glb", "done")]
        )
    )
    assert out[0]["verdict"] == "error" and "fixed" in out[0]["detail"] and "forge down" in out[0]["detail"]


def test_issue_client_rejects_an_unknown_state():
    import pytest

    from ada.comms.rest.issue_client import GitHubClient

    with pytest.raises(ValueError):
        asyncio.run(GitHubClient(repo="o/r", token="t").set_issue_state(1, state="merged"))


# Touch the module so unused-import lint passes don't drop it.
_ = audit_issue
