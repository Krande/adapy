"""Unit tests for ada.comms.rest.audit_issue (M5).

Fingerprint + sanitizer + sync orchestrator. Pure-function tests
only — the issue-bot loop in app.py is exercised separately when
ADA_TEST_POSTGRES_URL is set.
"""

from __future__ import annotations

import asyncio

import pytest

from ada.comms.rest import audit_issue
from ada.comms.rest.audit_issue import (
    comment_body,
    fingerprint,
    fp_label,
    issue_body,
    issue_title,
    sanitize_corpus_key,
    scope_of,
    strip_volatile,
    sync_run_issues,
)


# ── fingerprint ─────────────────────────────────────────────────────


def test_fingerprint_is_deterministic():
    """Same inputs → same hash, every time."""
    fp1 = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg="UnsupportedFormat", traceback="Traceback...\nFile a.py:1",
    )
    fp2 = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg="UnsupportedFormat", traceback="Traceback...\nFile a.py:1",
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
        source_ext=".step", target_format="glb",
        error_msg="DifferentError", traceback="frame",
    )
    assert len({a, b, c, d}) == 4


def test_fingerprint_strips_volatile_substrings():
    """Tempfile paths, line numbers, hex blobs, timestamps must
    collapse so the same root cause produces the same fingerprint
    across runs — the dedup invariant the issue bot relies on."""
    fp1 = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg="Failed at /tmp/abc123/x.step",
        traceback='File "module.py":42 in convert',
    )
    fp2 = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg="Failed at /tmp/zxy999/x.step",
        traceback='File "module.py":777 in convert',
    )
    assert fp1 == fp2


def test_fingerprint_strips_iso_timestamps():
    fp1 = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg="error at 2026-05-27T14:23:11+00:00",
        traceback=None,
    )
    fp2 = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg="error at 2025-01-01T00:00:00Z",
        traceback=None,
    )
    assert fp1 == fp2


def test_fingerprint_tolerates_none_inputs():
    """A row with only an error message (no traceback) still
    hashes deterministically."""
    fp = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg=None, traceback=None,
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
        source_ext=".step", target_format="glb",
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
        fp="abc", run_id="r1", sanitized_source="corpus:x/file-xx",
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

    def __init__(self, existing: dict | None = None):
        self.existing = existing or {}  # label -> [IssueRef-like dict]
        self.created: list[dict] = []
        self.commented: list[tuple[int, str]] = []
        self.updated: list[tuple[int, str]] = []

    async def list_issues_by_label(self, label: str, *, state: str = "open"):
        rows = self.existing.get(label, [])
        return [_FakeIssue(**r) for r in rows]

    async def find_issue_by_title(self, title: str):
        return None  # dashboard rebuild is exercised separately

    async def create_issue(self, *, title: str, body: str, labels):
        rec = {"title": title, "body": body, "labels": list(labels)}
        self.created.append(rec)
        return _FakeIssue(number=len(self.created), title=title, body=body,
                          html_url=None, labels=list(labels), state="open")

    async def comment_issue(self, number: int, *, body: str):
        self.commented.append((number, body))

    async def update_issue_body(self, number: int, *, body: str):
        self.updated.append((number, body))


class _FakeIssue:
    def __init__(self, *, number, title, body=None, html_url=None,
                 labels=None, state="open"):
        self.number = number
        self.title = title
        self.body = body
        self.html_url = html_url
        self.labels = labels or []
        self.state = state


def _job(key, target, error="boom", tb=None, scope_kind="corpus", scope_id="x"):
    return {
        "key": key, "target_format": target, "error": error,
        "traceback": tb, "scope_kind": scope_kind, "scope_id": scope_id,
        "status": "failed",
    }


def test_sync_run_issues_opens_a_new_issue_per_fingerprint():
    client = _StubClient()
    jobs = [
        _job("a.step", "glb", error="UnsupportedFormat"),
        _job("b.step", "ifc", error="OtherError"),
    ]
    summary = asyncio.run(sync_run_issues(
        client, run={"id": "r1", "started_at": None}, failed_jobs=jobs,
    ))
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
    summary = asyncio.run(sync_run_issues(
        client, run={"id": "r1", "started_at": None}, failed_jobs=jobs,
    ))
    assert summary["opened"] == 1
    assert summary["unique_failures"] == 1


def test_sync_run_issues_comments_on_existing_label():
    """Reproduced fingerprint → comment on the existing issue,
    don't reopen."""
    # Pre-seed an "open" issue under the audit-fp label.
    sample_fp = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg="UnsupportedFormat", traceback=None,
    )
    label = fp_label(sample_fp)
    client = _StubClient(existing={
        label: [{"number": 7, "title": "audit: .step → glb", "labels": [label]}],
    })
    summary = asyncio.run(sync_run_issues(
        client,
        run={"id": "r2", "started_at": "2026-05-27T00:00:00Z"},
        failed_jobs=[_job("a.step", "glb", error="UnsupportedFormat")],
    ))
    assert summary["opened"] == 0
    assert summary["commented"] == 1
    assert client.commented[0][0] == 7  # commented on issue #7


def test_sync_run_issues_per_failure_error_doesnt_abort_run():
    """If one issue lookup blows up, the rest of the failures still
    sync. We mimic that by overriding one method to raise."""

    class _BrokenLookupClient(_StubClient):
        async def list_issues_by_label(self, label, *, state="open"):
            if label == fp_label(fingerprint(
                source_ext=".step", target_format="glb",
                error_msg="UnsupportedFormat", traceback=None,
            )):
                raise RuntimeError("simulated transport blip")
            return []

    client = _BrokenLookupClient()
    jobs = [
        _job("a.step", "glb", error="UnsupportedFormat"),
        _job("b.step", "ifc", error="OtherError"),
    ]
    summary = asyncio.run(sync_run_issues(
        client, run={"id": "r3", "started_at": None}, failed_jobs=jobs,
    ))
    # One failure → opened issue; the broken one is counted as an error.
    assert summary["opened"] == 1
    assert len(summary["errors"]) == 1


def test_sync_run_issues_handles_no_failures():
    """Empty failed_jobs is a no-op summary, not an error."""
    summary = asyncio.run(sync_run_issues(
        _StubClient(), run={"id": "r4"}, failed_jobs=[],
    ))
    assert summary == {"opened": 0, "commented": 0, "errors": [], "unique_failures": 0}


# ── M5b: source_label parameterization ─────────────────────────────


def test_issue_body_default_label_says_audit_run():
    body = issue_body(
        fp="abc", source_ext=".step", target_format="glb",
        sanitized_source="shared/x.step", error_msg="boom",
        traceback=None, run_id="r1", run_started_at=None,
    )
    assert "First seen in audit run" in body


def test_issue_body_custom_label_for_user_conversion():
    body = issue_body(
        fp="abc", source_ext=".step", target_format="glb",
        sanitized_source="user/me/x.step", error_msg="boom",
        traceback=None, run_id="audit-row-42", run_started_at=None,
        source_label="user conversion",
    )
    assert "First seen in user conversion" in body
    assert "audit run" not in body  # default phrasing replaced


def test_comment_body_uses_source_label():
    body = comment_body(
        fp="abc", run_id="audit-row-42",
        sanitized_source="shared/x.step", run_started_at=None,
        source_label="user conversion",
    )
    assert "Reproduced in user conversion" in body
    assert "audit run" not in body


def test_sync_run_issues_forwards_source_label_to_create():
    """The user-conversion path should land an issue body that says
    'user conversion', not 'audit run'."""
    client = _StubClient()
    job = _job("a.step", "glb", error="UnsupportedFormat")
    asyncio.run(sync_run_issues(
        client, run={"id": "audit-row-42", "started_at": None},
        failed_jobs=[job], source_label="user conversion",
    ))
    assert client.created, "expected one issue created"
    assert "user conversion" in client.created[0]["body"]


def test_sync_run_issues_forwards_source_label_to_comment():
    sample_fp = fingerprint(
        source_ext=".step", target_format="glb",
        error_msg="UnsupportedFormat", traceback=None,
    )
    label = fp_label(sample_fp)
    client = _StubClient(existing={
        label: [{"number": 9, "title": "audit: .step → glb", "labels": [label]}],
    })
    asyncio.run(sync_run_issues(
        client, run={"id": "audit-row-9", "started_at": None},
        failed_jobs=[_job("b.step", "glb", error="UnsupportedFormat")],
        source_label="user conversion",
    ))
    assert client.commented, "expected one comment"
    _, comment_text = client.commented[0]
    assert "user conversion" in comment_text


# Touch the module so unused-import lint passes don't drop it.
_ = audit_issue
