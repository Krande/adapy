"""Pure helpers for the audit-run → issue-tracker bridge (M5 of
plan/v2/notes_admin_audit_panel.md).

Two responsibilities, both deterministic and free of I/O:

* :func:`fingerprint` — collapse a job failure to a 16-char hex
  identifier that survives transient noise (tempfile paths, line
  numbers, timestamps, hex blobs). Failures with the same root
  cause collapse to the same fingerprint across audit runs, which
  is what powers the dedup logic in the issue-bot ("does an
  ``audit-fp:<hash>`` label already exist? then comment instead of
  reopen").

* :func:`sanitize_corpus_key` — strip proprietary filenames from
  issue bodies. A corpus may carry files customers don't want
  echoed into a public bug tracker, so the issue body refers to
  them by a short content hash. The admin can map the hash back to
  the real file via the audit UI.

Tested end-to-end in tests/comms/rest/test_audit_issue.py — keep
both functions pure (no DB, no HTTP, no clock) so the tests stay
unit-shaped.
"""

from __future__ import annotations

import hashlib
import re

# Volatile substring patterns. Each pattern is applied with a fixed
# replacement so two failures that differ only in those substrings
# end up with the same normalised form, and therefore the same
# fingerprint.
#
# The order matters slightly: tempfile paths can contain digits, so
# we strip those before number-runs, and timestamps include colons +
# digits, so we strip them before generic ``:<n>`` line-numbers.
_VOLATILE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # ISO-8601 / RFC-3339 timestamps. Catches things like
    # "2026-05-27T14:23:11.918432+00:00" or "2026-05-27 14:23:11".
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:?\d{2}|Z)?"), "<ts>"),
    # Bare ISO dates (without a time component).
    (re.compile(r"\d{4}-\d{2}-\d{2}"), "<date>"),
    # /tmp/<anything> and /var/folders/<anything> (macOS tempdir).
    (re.compile(r"/(?:tmp|var/folders)/[^\s'\"<>]+"), "/tmp/<x>"),
    # Long hex runs — UUIDs without dashes, sha256 digests, etc.
    # 8+ hex chars in a row catches all of those without eating
    # short error codes like "0xFF".
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "<hex>"),
    # ``file.py:123`` / ``"module.py":42`` line-number references in
    # tracebacks. Plain ``:\d+`` runs after the timestamp pattern so
    # HH:MM:SS substrings have already been replaced; what's left is
    # primarily line numbers (and the occasional port, which is fine
    # — the fingerprint loses negligible signal by treating them
    # equivalently).
    (re.compile(r":\d+"), ":<n>"),
    # Memory addresses (``0x7f3e9a8b6c00``).
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "<addr>"),
)


def strip_volatile(s: str) -> str:
    """Apply every :data:`_VOLATILE_PATTERNS` substitution to ``s``.

    Exposed for unit tests that want to drill into the
    normalisation step independently of :func:`fingerprint`.
    """
    out = s
    for pattern, repl in _VOLATILE_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _last_traceback_frame(traceback: str | None) -> str:
    """Return the last meaningful traceback frame.

    Conversion errors often share a long stack but diverge only on
    the deepest user-code frame. We want the fingerprint to focus
    on the deepest frame so adjacent transient errors don't bucket
    together. ``last_frame`` is approximated as the final non-empty
    line — good enough for both Python tracebacks and Java-style
    chains.
    """
    if not traceback:
        return ""
    lines = [line.rstrip() for line in traceback.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def fingerprint(
    *,
    source_ext: str,
    target_format: str,
    error_msg: str | None,
    traceback: str | None,
) -> str:
    """Compute a 16-char hex fingerprint of a job failure.

    Two failures with the same ``(source_ext, target_format)`` and
    structurally identical messages + last traceback frame collapse
    to the same fingerprint. The hex is the first 16 chars of the
    sha256 digest of the normalised concatenation — collision-
    resistant in practice for the volumes the audit panel
    produces.

    ``error_msg`` and ``traceback`` may be ``None``; both default
    to empty strings so a failure that recorded only one of them
    still hashes deterministically.
    """
    norm_msg = strip_volatile((error_msg or "").strip())
    norm_top = strip_volatile(_last_traceback_frame(traceback))
    payload = (
        f"{(source_ext or '').strip().lower()}|" f"{(target_format or '').strip().lower()}|" f"{norm_msg}|{norm_top}"
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return digest[:16]


def sanitize_corpus_key(scope: str, key: str) -> str:
    """Map a real corpus filename to a sanitised public form.

    The plan calls out that corpus filenames may be proprietary. We
    can't echo them into a bug tracker, so the public reference is
    ``corpus:<slug>/file-<short-hash>`` instead of the real path.
    The admin can reverse the mapping via the audit UI (the audit
    log row still carries the real key).

    Scopes other than corpus pass through unchanged — user / shared
    / project filenames aren't subject to the same restriction.

    ``key`` is hashed; the same key always produces the same
    placeholder so consecutive runs against the same corpus file
    reuse the same label in the bug tracker.
    """
    if not scope.startswith("corpus:"):
        return f"{scope}/{key}" if scope and key else (key or scope)
    slug = scope[len("corpus:") :]
    file_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return f"corpus:{slug}/file-{file_hash}"


# ── Issue body assembly ────────────────────────────────────────────


_TRACEBACK_EXCERPT_LINES = 12


def _excerpt(traceback: str | None) -> str:
    """Return the trailing N lines of a traceback, fenced as code.

    Full tracebacks can run to hundreds of lines and don't add
    signal to a bug report — the deepest frames are what a triager
    needs. Empty input collapses to an empty string so we don't
    emit a stray fenced block.
    """
    if not traceback:
        return ""
    lines = traceback.rstrip().splitlines()
    tail = lines[-_TRACEBACK_EXCERPT_LINES:]
    return "```\n" + "\n".join(tail) + "\n```"


def issue_title(*, source_ext: str, target_format: str, fp: str) -> str:
    """Stable title for a per-fingerprint issue. The hash suffix
    makes the title self-identifying when the label fades from
    view in a long list."""
    return f"audit: {source_ext} → {target_format} regression [{fp}]"


def issue_body(
    *,
    fp: str,
    source_ext: str,
    target_format: str,
    sanitized_source: str,
    error_msg: str | None,
    traceback: str | None,
    run_id: str,
    run_started_at: str | None,
    source_label: str = "audit run",
) -> str:
    """Markdown body for a freshly-opened issue.

    Includes the structural identity (so triagers can see "this is
    .step → .glb failing on the top-level converter call without
    opening the audit panel"), the original error message, and a
    short traceback excerpt.

    ``source_label`` is the human-readable name for what triggered
    the failure — "audit run" for sweep dispatch, "user conversion"
    for a regular /convert that failed. Default keeps the existing
    audit-run wording so old call sites stay correct.
    """
    parts: list[str] = []
    parts.append(f"**Fingerprint:** `{fp}`")
    parts.append(f"**Conversion:** `{source_ext}` → `{target_format}`")
    parts.append(f"**First source:** `{sanitized_source}`")
    parts.append(f"**First seen in {source_label}:** `{run_id}`")
    if run_started_at:
        parts.append(f"**Failure observed:** {run_started_at}")
    if error_msg:
        parts.append("\n**Error message:**\n")
        parts.append(f"```\n{error_msg.strip()}\n```")
    excerpt = _excerpt(traceback)
    if excerpt:
        parts.append("\n**Traceback excerpt:**\n")
        parts.append(excerpt)
    parts.append(
        "\n---\n"
        "_Auto-opened by the ada-py issue bot. Comments listing "
        "further reproductions are appended whenever the same "
        "fingerprint trips again — from either an audit sweep or "
        "a regular user conversion. The bot does not auto-close; "
        "close the issue manually once the root cause ships._"
    )
    return "\n".join(parts)


def comment_body(
    *,
    fp: str,
    run_id: str,
    sanitized_source: str,
    run_started_at: str | None,
    source_label: str = "audit run",
) -> str:
    """Comment posted on an existing audit-fp issue when the
    fingerprint reproduces. Short by design — a triager wants the
    count to grow without reading duplicate stacks.

    ``source_label`` lets the comment say "Reproduced in user
    conversion `<id>`" for ad-hoc failures vs the original "audit
    run" phrasing for batch sweeps."""
    when = f" at {run_started_at}" if run_started_at else ""
    return (
        f"Reproduced in {source_label} `{run_id}`{when}.\n\n"
        f"- Source: `{sanitized_source}`\n"
        f"- Fingerprint: `{fp}`"
    )


def dashboard_title() -> str:
    """Single fixed title for the rebuilt dashboard issue. The bot
    finds it by exact-title match if no audit-status label exists
    yet."""
    return "Audit status (auto-managed)"


def dashboard_body(
    *,
    open_fingerprints: list[dict],
    last_run_id: str | None,
    last_run_status: str | None,
    last_run_finished_at: str | None,
) -> str:
    """Markdown body for the dashboard issue.

    ``open_fingerprints`` is a list of dicts shaped
    ``{"fp": str, "title": str, "url": str | None, "count": int,
       "source_ext": str, "target_format": str}``.

    Renders as a renovate-style checklist sorted by reproduction
    count desc — most-repeated regressions surface first.
    """
    lines: list[str] = []
    lines.append("This issue is auto-rebuilt on every audit-run finish.")
    lines.append("")
    if last_run_id:
        lines.append(
            f"**Last run:** `{last_run_id}`"
            f" — status `{last_run_status or 'unknown'}`"
            + (f" — finished {last_run_finished_at}" if last_run_finished_at else "")
        )
        lines.append("")
    if not open_fingerprints:
        lines.append("No open audit regressions. The matrix is clean.")
        return "\n".join(lines)
    lines.append(f"**Open audit regressions:** {len(open_fingerprints)}")
    lines.append("")
    sorted_fps = sorted(
        open_fingerprints,
        key=lambda f: f.get("count", 0),
        reverse=True,
    )
    for f in sorted_fps:
        title = f.get("title") or f.get("fp", "")
        link = f.get("url")
        count = f.get("count", 0)
        line = f"- [ ] [{title}]({link})" if link else f"- [ ] {title}"
        if count > 1:
            line += f" — reproduced {count}×"
        lines.append(line)
    return "\n".join(lines)


# ── Sync orchestrator ──────────────────────────────────────────────


# Source extension is recovered from the audit-log row's ``key`` —
# the storage layer doesn't carry a separate source_ext column, but
# the suffix is stable. Keep this helper local so the dispatcher
# doesn't have to import pathlib just to grab a suffix.
def _ext_of(key: str | None) -> str:
    if not key:
        return ""
    idx = key.rfind(".")
    if idx < 0:
        return ""
    return key[idx:].lower()


def scope_of(job: dict) -> str:
    """Reconstruct the scope token from the audit_log row's
    ``scope_kind`` / ``scope_id`` columns. Returns the same wire
    format the frontend uses ('shared', 'user:<sub>', 'project:<id>',
    'corpus:<slug>').
    """
    kind = (job.get("scope_kind") or "").strip()
    sid = (job.get("scope_id") or "").strip()
    if kind == "shared":
        return "shared"
    if kind == "user":
        return f"user:{sid}" if sid else "user:"
    if kind in ("project", "corpus"):
        return f"{kind}:{sid}"
    return kind or "shared"


_AUDIT_LABEL = "audit"
_DASHBOARD_LABEL = "audit-dashboard"


def fp_label(fp: str) -> str:
    """Standard label name embedding the fingerprint. The bot finds
    open issues by exact-label match on this string, so any change
    here must be considered carefully (existing issues won't be
    found by the new label name)."""
    return f"audit-fp:{fp}"


async def sync_run_issues(
    client,
    *,
    run: dict,
    failed_jobs: list[dict],
    source_label: str = "audit run",
) -> dict:
    """Sync one audit-run's failures against the configured forge.

    For each failed job: fingerprint the failure, look up an open
    issue by ``audit-fp:<hash>`` label, post a reproduction comment
    if it exists or open a new issue if not. Returns a summary
    dict (``opened``, ``commented``, ``errors``) so the caller can
    log a one-liner per run.

    ``source_label`` controls the wording in the issue body /
    comment — defaults to "audit run" for batch dispatch, callers
    syncing a single user-driven failure pass "user conversion"
    instead.

    The client conforms to :class:`ada.comms.rest.issue_client.GitForgeClient`.
    Errors on individual issues are caught + counted; one broken cell
    doesn't abort the whole sync (the dashboard rebuild step still
    runs against whatever did succeed).
    """
    opened = 0
    commented = 0
    errors: list[str] = []
    # Deduplicate by fingerprint within the run so multiple cells
    # tripping the same regression produce one comment, not N.
    seen_fps: dict[str, dict] = {}
    for job in failed_jobs:
        ext = _ext_of(job.get("key"))
        target = (job.get("target_format") or "").strip().lower()
        fp = fingerprint(
            source_ext=ext,
            target_format=target,
            error_msg=job.get("error"),
            traceback=job.get("traceback"),
        )
        scope = scope_of(job)
        sanitized = sanitize_corpus_key(scope, job.get("key") or "")
        if fp in seen_fps:
            continue
        seen_fps[fp] = {
            "fp": fp,
            "source_ext": ext,
            "target_format": target,
            "sanitized_source": sanitized,
            "error": job.get("error"),
            "traceback": job.get("traceback"),
        }

    for fp, ctx in seen_fps.items():
        label = fp_label(fp)
        try:
            existing = await client.list_issues_by_label(label, state="open")
        except Exception as exc:  # IssueClientError or transport
            errors.append(f"lookup {fp}: {exc}")
            continue
        try:
            if existing:
                issue = existing[0]
                await client.comment_issue(
                    issue.number,
                    body=comment_body(
                        fp=fp,
                        run_id=run["id"],
                        sanitized_source=ctx["sanitized_source"],
                        run_started_at=run.get("started_at"),
                        source_label=source_label,
                    ),
                )
                commented += 1
            else:
                await client.create_issue(
                    title=issue_title(
                        source_ext=ctx["source_ext"],
                        target_format=ctx["target_format"],
                        fp=fp,
                    ),
                    body=issue_body(
                        fp=fp,
                        source_ext=ctx["source_ext"],
                        target_format=ctx["target_format"],
                        sanitized_source=ctx["sanitized_source"],
                        error_msg=ctx["error"],
                        traceback=ctx["traceback"],
                        run_id=run["id"],
                        run_started_at=run.get("started_at"),
                        source_label=source_label,
                    ),
                    labels=[
                        _AUDIT_LABEL,
                        label,
                        f"target:{ctx['target_format']}",
                    ],
                )
                opened += 1
        except Exception as exc:
            errors.append(f"sync {fp}: {exc}")
            continue

    return {
        "opened": opened,
        "commented": commented,
        "errors": errors,
        "unique_failures": len(seen_fps),
    }


async def rebuild_dashboard_issue(
    client,
    *,
    last_run: dict | None,
) -> dict:
    """Rebuild the single "Audit status" dashboard issue.

    Lists every currently-open ``audit-fp:*`` issue, sorts by
    reproduction count (estimated from comment counts where the
    client supports it; falls back to 1 otherwise), and either
    updates the existing dashboard issue's body or opens a fresh
    one labelled ``audit-dashboard``.

    Returns a small status dict so the caller can log + surface in
    the UI ("dashboard updated, 4 regressions tracked").
    """
    try:
        labelled = await client.list_issues_by_label(_AUDIT_LABEL, state="open")
    except Exception as exc:
        return {"updated": False, "error": f"label lookup failed: {exc}"}

    open_fps: list[dict] = []
    for issue in labelled:
        fp = None
        for lab in issue.labels:
            if lab and lab.startswith("audit-fp:"):
                fp = lab[len("audit-fp:") :]
                break
        if fp is None:
            continue
        open_fps.append(
            {
                "fp": fp,
                "title": issue.title,
                "url": issue.html_url,
                # Without a per-issue comments call we approximate count
                # as 1 — the body has the latest reproduction and the
                # comments timeline holds the rest, so the user sees the
                # full reproduction count by clicking through.
                "count": 1,
            }
        )

    body = dashboard_body(
        open_fingerprints=open_fps,
        last_run_id=(last_run or {}).get("id"),
        last_run_status=(last_run or {}).get("status"),
        last_run_finished_at=(last_run or {}).get("finished_at"),
    )
    title = dashboard_title()
    try:
        existing = await client.find_issue_by_title(title)
    except Exception as exc:
        return {"updated": False, "error": f"dashboard lookup failed: {exc}"}

    try:
        if existing is not None:
            await client.update_issue_body(existing.number, body=body)
            return {"updated": True, "created": False, "tracked": len(open_fps)}
        await client.create_issue(
            title=title,
            body=body,
            labels=[_DASHBOARD_LABEL, _AUDIT_LABEL],
        )
        return {"updated": True, "created": True, "tracked": len(open_fps)}
    except Exception as exc:
        return {"updated": False, "error": f"dashboard write failed: {exc}"}
