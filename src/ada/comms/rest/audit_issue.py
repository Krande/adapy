"""Pure helpers for the audit-run → issue-tracker bridge (M5 of
the admin audit-panel design notes).

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


def fingerprint_job(job: dict) -> str:
    """:func:`fingerprint` of one audit_log row (``key``, ``target_format``, ``error``,
    ``traceback``) -- the one place a row becomes a fingerprint, shared by the sync, the
    ``issue_fp`` backfill and the recheck verdict so the three can never disagree."""
    return fingerprint(
        source_ext=_ext_of(job.get("key")),
        target_format=(job.get("target_format") or "").strip().lower(),
        error_msg=job.get("error"),
        traceback=job.get("traceback"),
    )


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
        "a regular user conversion. Once a fix ships, **Recheck** "
        "this issue from the admin audit panel: the bot re-runs "
        "every cell that reproduced it and closes the issue when "
        "they all pass._"
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
    skip_fps: frozenset[str] | set[str] = frozenset(),
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

    ``skip_fps`` are fingerprints this sync must leave alone: a recheck run's own
    fingerprints, which get a verdict comment instead of a reproduction comment.

    A fingerprint whose issue was CLOSED (by a recheck, or by hand) and that fails again is
    reopened with a comment saying so, rather than opened as a duplicate issue.

    The client conforms to :class:`ada.comms.rest.issue_client.GitForgeClient`.
    Errors on individual issues are caught + counted; one broken cell
    doesn't abort the whole sync (the dashboard rebuild step still
    runs against whatever did succeed).
    """
    opened = 0
    commented = 0
    reopened = 0
    errors: list[str] = []
    # Deduplicate by fingerprint within the run so multiple cells
    # tripping the same regression produce one comment, not N.
    seen_fps: dict[str, dict] = {}
    for job in failed_jobs:
        ext = _ext_of(job.get("key"))
        target = (job.get("target_format") or "").strip().lower()
        fp = fingerprint_job(job)
        scope = scope_of(job)
        sanitized = sanitize_corpus_key(scope, job.get("key") or "")
        if fp in seen_fps or fp in skip_fps:
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
            closed = [] if existing else await client.list_issues_by_label(label, state="closed")
            if existing or closed:
                issue = (existing or closed)[0]
                body = comment_body(
                    fp=fp,
                    run_id=run["id"],
                    sanitized_source=ctx["sanitized_source"],
                    run_started_at=run.get("started_at"),
                    source_label=source_label,
                )
                if existing:
                    commented += 1
                else:
                    await client.set_issue_state(issue.number, state="open")
                    body = "**Reopened: this fingerprint failed again after the issue was closed.**\n\n" + body
                    reopened += 1
                await client.comment_issue(issue.number, body=body)
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
        "reopened": reopened,
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


# ── Issue recheck ──────────────────────────────────────────────────
#
# A recheck re-runs every cell that ever reproduced a fingerprint, as an ordinary audit
# run with ``trigger=RECHECK_TRIGGER``, and when that run finishes the issue-bot gives the
# fingerprint a verdict and closes its issue if every cell passed.

RECHECK_TRIGGER = "issue-recheck"

_PASS_STATUSES = frozenset({"ok", "done"})
_FAIL_STATUSES = frozenset({"error", "failed"})
_WASM_POOL = "wasm"


def recheck_cells(
    rows: list[dict],
    *,
    failure_slug: str,
) -> tuple[dict[tuple[str, str | None], list[tuple[str, str]]], list[str]]:
    """Turn the failing rows behind one fingerprint (newest first, as
    ``db.list_issue_fp_rows`` returns them) into the cells a recheck re-runs.

    Returns ``(groups, skipped)``: ``groups`` maps ``(scope, worker_pool)`` -- one audit run
    each, since a run has one scope and one pool -- to its ``(key, target)`` cells, and
    ``skipped`` names, sanitised, each failing cell that cannot be re-run and why.

    Where a cell is re-run decides whose files a recheck touches, so the order is strict:

    * the failure-corpus copy (``failure_key``) when capture took one -- the bytes that
      actually failed, in an admin-only scope, so rechecking a user's failure never reads
      or writes that user's scope;
    * a corpus row in place -- corpus files are frozen;
    * a shared row in place;
    * otherwise (a user or project file with no preserved copy) the cell is skipped.

    A cell that failed in the browser engine is skipped too: the server cannot re-run it.
    """
    groups: dict[tuple[str, str | None], list[tuple[str, str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    covered: set[tuple] = set()
    skipped_reasons: dict[tuple, str] = {}
    for r in rows:
        kind = (r.get("scope_kind") or "").strip()
        target = (r.get("target_format") or "").strip().lower()
        original = (kind, r.get("scope_id"), r.get("key"), target)
        pool = r.get("worker_pool")
        resolved: tuple[str, str] | None = None
        reason = ""
        if isinstance(pool, str) and pool.strip().lower() == _WASM_POOL:
            reason = "failed in the browser engine, which the server cannot re-run"
        elif r.get("failure_key"):
            resolved = (f"corpus:{failure_slug}", r["failure_key"])
        elif kind == "corpus":
            resolved = (f"corpus:{r.get('scope_id') or ''}", r.get("key") or "")
        elif kind == "shared":
            resolved = ("shared", r.get("key") or "")
        else:
            reason = f"no preserved copy of its {kind or 'unknown'}-scope source"
        if resolved is None or not resolved[1] or not target:
            skipped_reasons.setdefault(original, reason or "no source key or target")
            continue
        covered.add(original)
        scope_str, key = resolved
        if (scope_str, key, target) in seen:
            continue
        seen.add((scope_str, key, target))
        groups.setdefault((scope_str, pool), []).append((key, target))
    skipped = [
        f"{sanitize_corpus_key(scope_of({'scope_kind': o[0], 'scope_id': o[1]}), o[2] or '')} → {o[3]}: {why}"
        for o, why in skipped_reasons.items()
        if o not in covered
    ]
    return groups, skipped


def recheck_verdict(fp: str, cells: list[tuple[str, str]], run_jobs: list[dict]) -> dict:
    """Judge one fingerprint from a finished recheck run.

    ``run_jobs`` are the run's audit_log rows (oldest first; failed ones need ``error`` and
    ``traceback`` to be fingerprinted). Each requested cell's newest row counts:

    * ``fixed`` -- every cell passed;
    * ``reproduced`` -- any cell failed with ``fp`` again;
    * ``changed`` -- no cell reproduced ``fp``, but one failed with another fingerprint;
    * ``unverifiable`` -- nothing failed, but a cell is missing from the run (its source is
      gone) or did not finish, so the run proves nothing about it.
    """
    latest: dict[tuple[str, str], dict] = {}
    for j in run_jobs:
        latest[(j.get("key") or "", (j.get("target_format") or "").strip().lower())] = j
    passed = reproduced = changed = missing = 0
    new_fps: list[str] = []
    for key, target in cells:
        j = latest.get((key, target.lower()))
        status = (j or {}).get("status")
        if status in _PASS_STATUSES:
            passed += 1
        elif status in _FAIL_STATUSES:
            got = fingerprint_job(j)
            if got == fp:
                reproduced += 1
            else:
                changed += 1
                if got not in new_fps:
                    new_fps.append(got)
        else:
            missing += 1
    if reproduced:
        verdict = "reproduced"
    elif changed:
        verdict = "changed"
    elif missing or not cells:
        verdict = "unverifiable"
    else:
        verdict = "fixed"
    return {
        "verdict": verdict,
        "total": len(cells),
        "passed": passed,
        "reproduced": reproduced,
        "changed": changed,
        "missing": missing,
        "new_fps": new_fps,
    }


def recheck_summary(result: dict) -> str:
    """One line for the admin UI and the recheck row's ``verdict_detail``."""
    parts = [f"passed {result['passed']}/{result['total']}"]
    if result["reproduced"]:
        parts.append(f"reproduced {result['reproduced']}")
    if result["changed"]:
        parts.append(f"failed differently {result['changed']}")
    if result["missing"]:
        parts.append(f"not re-run {result['missing']}")
    return ", ".join(parts)


_VERDICT_HEADLINE = {
    "fixed": "**Recheck passed — closing.** Every cell that reproduced this fingerprint now converts.",
    "reproduced": "**Recheck: still reproduces.** Leaving this open.",
    "changed": (
        "**Recheck: no longer this failure, but not passing either.** Leaving this open; "
        "the new failure is tracked under its own fingerprint."
    ),
    "unverifiable": (
        "**Recheck inconclusive.** Some cells could not be re-run (source gone or the cell "
        "did not finish), so this stays open."
    ),
}


def recheck_comment(*, fp: str, run_id: str, result: dict, worker_image_tags: list[str]) -> str:
    """Comment posted on the issue when a recheck run finishes."""
    lines = [_VERDICT_HEADLINE[result["verdict"]], ""]
    lines.append(f"- Recheck run: `{run_id}`")
    lines.append(f"- Cells: {recheck_summary(result)}")
    if worker_image_tags:
        lines.append("- Worker image: " + ", ".join(f"`{t}`" for t in worker_image_tags))
    if result["new_fps"]:
        lines.append("- Now failing as: " + ", ".join(f"`{fp_label(f)}`" for f in result["new_fps"]))
    lines.append(f"- Fingerprint: `{fp}`")
    return "\n".join(lines)


async def publish_recheck_verdicts(
    client,
    *,
    run: dict,
    rechecks: list[dict],
    run_jobs: list[dict],
) -> list[dict]:
    """Judge every fingerprint a finished recheck run was checking, comment the verdict on
    its open issue, and close the issue when the verdict is ``fixed``.

    Returns ``[{"id", "verdict", "detail"}]`` per recheck row for the caller to persist. A
    forge error on one fingerprint is recorded as verdict ``error`` (with the verdict it
    would have had in ``detail``) and does not stop the others. A fingerprint with no open
    issue -- closed by hand meanwhile -- still gets its verdict recorded."""
    tags = sorted({j["worker_image_tag"] for j in run_jobs if j.get("worker_image_tag")})
    out: list[dict] = []
    for rc in rechecks:
        fp = rc["fp"]
        result = recheck_verdict(fp, [tuple(c) for c in rc["cells"]], run_jobs)
        detail = recheck_summary(result)
        try:
            issues = await client.list_issues_by_label(fp_label(fp), state="open")
            if issues:
                number = issues[0].number
                await client.comment_issue(
                    number,
                    body=recheck_comment(fp=fp, run_id=run["id"], result=result, worker_image_tags=tags),
                )
                if result["verdict"] == "fixed":
                    await client.set_issue_state(number, state="closed")
        except Exception as exc:
            out.append({"id": rc["id"], "verdict": "error", "detail": f"{result['verdict']} ({detail}); forge: {exc}"})
            continue
        out.append({"id": rc["id"], "verdict": result["verdict"], "detail": detail})
    return out
