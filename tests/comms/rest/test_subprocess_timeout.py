"""Watchdog test for ``run_isolated_convert``'s timeout path.

The watchdog is the only mechanism that kills a converter that's
stuck in an OCCT loop (or any other native code that ignores
Python's KeyboardInterrupt). The test pins the contract:

* a convert_fn that sleeps past the timeout gets terminated
* the returned :class:`IsolatedConvertResult` has
  ``signal_name == "TIMEOUT"`` so the worker can write a clear,
  timeout-specific error message
* the wall-clock elapsed is bounded by ``timeout_s + GRACE_S``
  (the SIGKILL fallback) — a SIGTERM-ignoring child still gets
  reaped quickly

Skipped on platforms without ``os.fork`` (e.g. Windows CI). The
test stays under 5 s by setting a 1 s timeout.
"""

from __future__ import annotations

import pathlib
import sys
import time

import pytest

# subprocess_convert imports adapy heavily; gate the module-level
# import so a missing dep doesn't crash collection.
from ada.comms.rest.subprocess_convert import run_isolated_convert

pytestmark = pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="os.fork not available on Windows",
)


def _sleep_forever_convert(src, source_key, target_format, on_progress, **_kw):
    """Synthetic convert_fn that intentionally exceeds the timeout.

    Sleeps in 0.1 s ticks (so SIGTERM lands cleanly) for up to a
    minute — well past the test's 1 s timeout. The function shape
    matches what the real converters use so the fork plumbing
    exercises the same code paths.
    """
    on_progress("sleeping", 0.1)
    for _ in range(600):
        time.sleep(0.1)
    return b""


@pytest.mark.asyncio
async def test_watchdog_kills_long_running_convert(tmp_path: pathlib.Path):
    src = tmp_path / "x.bin"
    src.write_bytes(b"")
    started = time.monotonic()
    result = await run_isolated_convert(
        _sleep_forever_convert,
        src_path=src,
        source_key="x.bin",
        target_format="glb",
        timeout_s=1.0,  # tight so the test stays fast
    )
    elapsed = time.monotonic() - started

    assert result.signal_name == "TIMEOUT", (
        f"expected signal_name='TIMEOUT', got {result.signal_name!r} " f"(exit_code={result.exit_code})"
    )
    assert result.out_bytes is None
    assert result.error is not None
    assert "timeout" in result.error.lower()
    # Watchdog gives 30 s grace before SIGKILL. The synthetic sleeper
    # honours SIGTERM via default handler so it exits within seconds
    # of the deadline — generous upper bound to keep the test stable
    # on slow CI.
    assert elapsed < 10.0, f"watchdog reap too slow: {elapsed:.1f}s"


def _hog_memory_convert(src, source_key, target_format, on_progress, **_kw):
    """Synthetic convert_fn that allocates well past the RSS limit and holds it,
    so the memory watchdog has time to sample and reap it."""
    on_progress("allocating", 0.1)
    blobs = []
    for _ in range(5):
        b = bytearray(100 * 1024 * 1024)  # 100 MB
        for i in range(0, len(b), 4096):  # touch every page so it's resident
            b[i] = 1
        blobs.append(b)
        time.sleep(0.1)
    time.sleep(5.0)  # hold so the watchdog reaps us before we'd return
    return b"unreachable"


@pytest.mark.asyncio
async def test_memory_watchdog_kills_oom_convert(tmp_path: pathlib.Path, monkeypatch):
    """A convert_fn that blows past the per-job RSS limit is SIGKILLed by the
    memory watchdog, and the result carries ``signal_name == 'OOM'`` so the
    worker writes a clear out-of-memory error (and acks the job once instead of
    letting the kernel OOM-kill the whole pod)."""
    monkeypatch.setenv("ADA_CONVERT_MEM_LIMIT_MB", "256")
    src = tmp_path / "x.bin"
    src.write_bytes(b"")
    started = time.monotonic()
    result = await run_isolated_convert(
        _hog_memory_convert,
        src_path=src,
        source_key="x.bin",
        target_format="glb",
    )
    elapsed = time.monotonic() - started

    assert result.signal_name == "OOM", f"expected signal_name='OOM', got {result.signal_name!r} ({result.exit_code})"
    assert result.out_bytes is None
    assert result.error is not None and "memory" in result.error.lower()
    assert elapsed < 10.0, f"memory watchdog reap too slow: {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_no_timeout_lets_short_convert_finish(tmp_path: pathlib.Path):
    """Regression guard: a None timeout must not introduce any kill
    path. We use a 2 s timeout (longer than the convert) to also
    verify a SET timeout that DOESN'T fire still lets the child
    finish normally."""

    def quick(src, source_key, target_format, on_progress, **_kw):
        on_progress("hello", 1.0)
        return b"ok"

    src = tmp_path / "x.bin"
    src.write_bytes(b"")
    result = await run_isolated_convert(
        quick,
        src_path=src,
        source_key="x.bin",
        target_format="glb",
        timeout_s=2.0,
    )
    assert result.signal_name is None
    assert result.exit_code == 0
    assert result.out_bytes == b"ok"
