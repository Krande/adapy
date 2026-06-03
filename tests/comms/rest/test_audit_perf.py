"""Unit tests for ada.comms.rest.audit_perf (M6).

Pure-function classifier — no DB, no clock. Aggregation SQL is
exercised separately via the live-Postgres path in test_admin.py.
"""

from __future__ import annotations

from ada.comms.rest.audit_perf import (
    DEFAULT_THRESHOLDS,
    SIGNAL_REASONS,
    annotate,
    classify_streaming_candidate,
    merged_thresholds,
)


def _cell(**overrides):
    """Helper: build a clean cell with non-flagging defaults, then
    overlay test-specific values. Keeps each test focused on the
    signal it exercises."""
    base = {
        "source_ext": ".step",
        "target_format": "glb",
        "sample_count": 50,
        "fail_count": 0,
        "ok_count": 50,
        "failure_rate": 0.0,
        "duration_ms_p50": 1000,
        "duration_ms_p95": 5000,
        "duration_ms_max": 8000,
        "peak_rss_kb_p50": 100_000,
        "peak_rss_kb_p95": 200_000,
        "peak_rss_max_kb": 250_000,
        "peak_rss_per_source_mb_p95": 5.0,
        "write_bytes_p50": 500_000,
        "write_bytes_p95": 1_000_000,
        "read_bytes_avg": 5_000_000,
    }
    base.update(overrides)
    return base


def test_clean_cell_is_not_a_candidate():
    verdict = classify_streaming_candidate(_cell())
    assert verdict == {"is_candidate": False, "signals": []}


def test_small_sample_never_flags():
    """A cell with <5 samples is treated as no-data — small sample
    p95/max would be unstable enough to flag false positives."""
    cell = _cell(
        sample_count=3,
        peak_rss_per_source_mb_p95=999.0,
        duration_ms_p95=999_000,
    )
    verdict = classify_streaming_candidate(cell)
    assert verdict["is_candidate"] is False


def test_rss_per_mb_signal_fires():
    cell = _cell(peak_rss_per_source_mb_p95=25.0)  # default threshold 20
    verdict = classify_streaming_candidate(cell)
    assert verdict["is_candidate"] is True
    assert "rss_per_source_mb_p95" in verdict["signals"]


def test_duration_signal_fires():
    cell = _cell(duration_ms_p95=70_000)  # 70s > default 60s
    verdict = classify_streaming_candidate(cell)
    assert "duration_s_p95" in verdict["signals"]


def test_peak_rss_max_signal_fires():
    # default peak_rss_max_mb = 3200 → 3200 MB == 3_276_800 KB
    cell = _cell(peak_rss_max_kb=4_000_000)
    verdict = classify_streaming_candidate(cell)
    assert "peak_rss_max_mb" in verdict["signals"]


def test_failure_rate_signal_fires():
    cell = _cell(failure_rate=0.10)  # 10% > default 5%
    verdict = classify_streaming_candidate(cell)
    assert "failure_rate" in verdict["signals"]


def test_multiple_signals_collect_all():
    cell = _cell(
        peak_rss_per_source_mb_p95=30.0,
        duration_ms_p95=120_000,
    )
    verdict = classify_streaming_candidate(cell)
    assert set(verdict["signals"]) == {
        "rss_per_source_mb_p95",
        "duration_s_p95",
    }


def test_threshold_overrides_can_disable_a_signal():
    """A relaxed threshold should suppress an otherwise-firing signal."""
    cell = _cell(peak_rss_per_source_mb_p95=25.0)
    verdict = classify_streaming_candidate(
        cell,
        thresholds={"rss_per_source_mb_p95": 100.0},
    )
    assert "rss_per_source_mb_p95" not in verdict["signals"]
    assert verdict["is_candidate"] is False


def test_threshold_overrides_can_tighten_a_signal():
    cell = _cell(failure_rate=0.02)  # 2% — well below default 5%
    verdict = classify_streaming_candidate(
        cell,
        thresholds={"failure_rate": 0.01},
    )
    assert "failure_rate" in verdict["signals"]


def test_merged_thresholds_drops_unknown_keys():
    """Typo'd keys mustn't shadow valid defaults — drop silently."""
    merged = merged_thresholds({"unknown_key": 123.0, "duration_s_p95": 10.0})
    assert "unknown_key" not in merged
    assert merged["duration_s_p95"] == 10.0
    # Untouched keys keep their defaults.
    assert merged["rss_per_source_mb_p95"] == DEFAULT_THRESHOLDS["rss_per_source_mb_p95"]


def test_merged_thresholds_accepts_string_numerics():
    """JSON-shaped string numbers ('20.0') should coerce cleanly."""
    merged = merged_thresholds({"duration_s_p95": "45"})
    assert merged["duration_s_p95"] == 45.0


def test_merged_thresholds_ignores_unparseable_strings():
    """A bad value ('hi') leaves the default in place — we never
    fall back to a partial config in a way that hides a typo."""
    merged = merged_thresholds({"duration_s_p95": "hi"})
    assert merged["duration_s_p95"] == DEFAULT_THRESHOLDS["duration_s_p95"]


def test_annotate_attaches_streaming_field_per_cell():
    cells = [
        _cell(source_ext=".step", target_format="glb"),
        _cell(source_ext=".ifc", target_format="glb", peak_rss_per_source_mb_p95=30.0),
    ]
    out = annotate(cells)
    assert out[0]["streaming"] == {"is_candidate": False, "signals": []}
    assert out[1]["streaming"]["is_candidate"] is True


def test_signal_reasons_covers_every_threshold():
    """SIGNAL_REASONS must have an entry per threshold key so the
    UI tooltip never falls back to displaying a raw key."""
    assert set(SIGNAL_REASONS.keys()) == set(DEFAULT_THRESHOLDS.keys())


def test_missing_metric_does_not_fire_signal():
    """A cell with NULL on a particular metric simply doesn't trip
    that signal — no crash, no false positive."""
    cell = _cell(peak_rss_per_source_mb_p95=None, duration_ms_p95=None)
    verdict = classify_streaming_candidate(cell)
    assert verdict["is_candidate"] is False


def test_cpu_fraction_signal_fires_when_io_bound():
    """A cell with low CPU fraction (mostly IO/wait) should fire
    the IO-bound signal."""
    cell = _cell(cpu_fraction=0.10)
    verdict = classify_streaming_candidate(cell)
    assert "cpu_fraction_max" in verdict["signals"]
    assert verdict["is_candidate"] is True


def test_cpu_fraction_signal_silent_when_cpu_bound():
    """A CPU-bound cell (cpu_fraction > threshold) should NOT
    trigger the IO-bound flag — the test must check directionality
    since cpu_fraction_max is the only inverse-direction signal."""
    cell = _cell(cpu_fraction=0.80)
    verdict = classify_streaming_candidate(cell)
    assert "cpu_fraction_max" not in verdict["signals"]


def test_cpu_fraction_null_does_not_fire():
    """NULL cpu_fraction (no timing samples) must not trip the
    signal — otherwise empty cells would always look IO-bound."""
    cell = _cell(cpu_fraction=None)
    verdict = classify_streaming_candidate(cell)
    assert "cpu_fraction_max" not in verdict["signals"]


def test_cpu_fraction_threshold_overridable():
    """Same override mechanism as the other thresholds."""
    cell = _cell(cpu_fraction=0.50)  # default would not fire
    verdict = classify_streaming_candidate(
        cell,
        thresholds={"cpu_fraction_max": 0.60},
    )
    assert "cpu_fraction_max" in verdict["signals"]
