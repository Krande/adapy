"""Pure helpers for the cross-conversion performance dashboard (M6
of plan/v2/notes_admin_audit_panel.md).

This module is intentionally I/O-free: the SQL aggregation lives in
:mod:`db` and the REST plumbing lives in :mod:`app`. Here we just
compute the streaming-candidate verdict from a metric snapshot + a
threshold dict so the same logic can be unit-tested against fixture
inputs without touching Postgres.

Layer 3 of the design — "is this converter a streaming candidate?":

    streaming candidate if any of:
      p95(peak_rss / source_size_mb) > 20    # memory amplification
      p95(elapsed_s)                  > 60   # slow at scale
      max(peak_rss)                   > 80% of worker mem limit
      failure_rate                    > 0.05 # OOM kills / timeouts

The cell metrics dict is whatever
:func:`ada.comms.rest.db.aggregate_conversion_metrics` produces, but
each lookup is defensive (``.get(...)``) so a partial row missing
one column doesn't crash the classifier.
"""

from __future__ import annotations

from typing import Iterable


# Defaults — match the plan's numbers. Each value is a *threshold*;
# the classifier flags a cell when the corresponding metric STRICTLY
# exceeds it (so a converter sitting exactly at 20× isn't called a
# candidate yet).
DEFAULT_THRESHOLDS: dict[str, float] = {
    # Peak RSS divided by source file size (MB). A 30× ratio means a
    # 100 MB input pushes 3 GB of working memory — strong streaming
    # signal regardless of absolute time.
    "rss_per_source_mb_p95": 20.0,
    # Wall-clock elapsed at the p95 of the cell's sample. >60s is the
    # boundary where users start hitting timeouts + where streaming
    # output buys back interactive latency.
    "duration_s_p95": 60.0,
    # Absolute peak_rss ceiling. Default expressed in MB to match
    # what an admin sees in the UI. Above 80% of typical worker mem
    # (default worker_mem_limit_mb=4096) the run is one OOM away.
    "peak_rss_max_mb": 3200.0,
    # Failure rate (fraction in [0, 1]). >5% sustained means the
    # converter is either OOMing or timing out — both streaming-
    # solvable, both worth surfacing.
    "failure_rate": 0.05,
    # CPU fraction = sum(cpu_user_ms + cpu_sys_ms) / sum(duration_ms).
    # Below this threshold the conversion is mostly waiting (IO,
    # network, etc.) rather than computing — async streaming /
    # parallel IO usually wins back wall-clock without a code
    # rewrite. Default 0.30 matches what a typical "blocked on
    # network reads" conversion looks like in our existing data.
    "cpu_fraction_max": 0.30,
}


# Human-readable explanations for each fired signal. Surfaced in the
# UI tooltip so the operator sees *why* a cell got the badge without
# having to cross-reference the thresholds.
SIGNAL_REASONS: dict[str, str] = {
    "rss_per_source_mb_p95": "p95 RSS / source MB exceeds threshold",
    "duration_s_p95": "p95 elapsed exceeds threshold",
    "peak_rss_max_mb": "max peak RSS approaches worker memory limit",
    "failure_rate": "failure rate exceeds threshold",
    "cpu_fraction_max": "CPU fraction below threshold — likely IO-bound",
}


def merged_thresholds(overrides: dict | None) -> dict[str, float]:
    """Layer admin-supplied overrides on top of the shipped defaults.

    Unknown keys in ``overrides`` are silently dropped — they'd
    never fire a signal anyway, so we'd rather not raise here and
    cause the classifier to mis-report when an admin typos a key.
    Numeric coercion is permissive: strings that ``float()`` accepts
    (e.g. from a JSON payload where the API client stringified them)
    are accepted.
    """
    out = dict(DEFAULT_THRESHOLDS)
    if not overrides:
        return out
    for k, v in overrides.items():
        if k not in DEFAULT_THRESHOLDS:
            continue
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def classify_streaming_candidate(
    cell: dict,
    *,
    thresholds: dict | None = None,
) -> dict:
    """Return ``{"is_candidate": bool, "signals": list[str]}`` for one cell.

    ``cell`` is a dict with whichever of these keys the aggregator
    filled in: ``peak_rss_per_source_mb_p95``, ``duration_ms_p95``,
    ``peak_rss_max_kb``, ``failure_rate``, ``sample_count``.
    Missing keys never fire — a cell with no data isn't a candidate.

    Returned ``signals`` lists the threshold keys that fired (in the
    order they're checked), so a UI can render one badge per signal
    or pick the strongest. Tooltips can reuse :data:`SIGNAL_REASONS`.

    Cells with fewer than 5 samples never flag — small samples make
    p95/max wildly unstable and we don't want to wave the streaming
    flag based on three noisy data points. The frontend can request
    a wider date window if it cares about a sparsely-used cell.
    """
    thr = merged_thresholds(thresholds)
    signals: list[str] = []
    sample_count = int(cell.get("sample_count") or 0)
    if sample_count < 5:
        return {"is_candidate": False, "signals": []}

    rss_per_mb = cell.get("peak_rss_per_source_mb_p95")
    if rss_per_mb is not None and rss_per_mb > thr["rss_per_source_mb_p95"]:
        signals.append("rss_per_source_mb_p95")

    dur_ms = cell.get("duration_ms_p95")
    if dur_ms is not None and (dur_ms / 1000.0) > thr["duration_s_p95"]:
        signals.append("duration_s_p95")

    rss_max = cell.get("peak_rss_max_kb")
    if rss_max is not None and (rss_max / 1024.0) > thr["peak_rss_max_mb"]:
        signals.append("peak_rss_max_mb")

    failure_rate = cell.get("failure_rate")
    if failure_rate is not None and failure_rate > thr["failure_rate"]:
        signals.append("failure_rate")

    # IO-bound signal: ``cpu_fraction`` strictly BELOW the threshold
    # is the inverse-direction check vs the other signals. We
    # surface it as an independent flag so the UI can render
    # "IO-bound" alongside "consider streaming" — they often
    # co-occur but aren't identical.
    cpu_fraction = cell.get("cpu_fraction")
    if cpu_fraction is not None and cpu_fraction < thr["cpu_fraction_max"]:
        signals.append("cpu_fraction_max")

    return {"is_candidate": bool(signals), "signals": signals}


def annotate(
    cells: Iterable[dict],
    *,
    thresholds: dict | None = None,
) -> list[dict]:
    """Walk a list of aggregator cells, attaching the classifier
    verdict to each. The caller can render the badge directly off
    the returned ``streaming`` field without round-tripping through
    a second call."""
    thr = merged_thresholds(thresholds)
    out = []
    for cell in cells:
        verdict = classify_streaming_candidate(cell, thresholds=thr)
        out.append({**cell, "streaming": verdict})
    return out


__all__ = [
    "DEFAULT_THRESHOLDS",
    "SIGNAL_REASONS",
    "merged_thresholds",
    "classify_streaming_candidate",
    "annotate",
]
