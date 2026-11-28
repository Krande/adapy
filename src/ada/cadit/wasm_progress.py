"""Stage + fraction progress for a browser (Pyodide) conversion.

:mod:`ada.cadit.wasm_convert` runs synchronously inside the browser's Web Worker, so the
only way the page learns how far a conversion has come is a callback the host bridges in:
``on_progress(stage, fraction)``, which the worker posts to the page as a ``progress``
message (the same message a core job's progress travels as). :class:`ConversionProgress`
wraps that callback so every caller reports the same way:

* the fraction never goes backwards (a sub-step reporting a lower value is lifted to the
  highest one told so far) and stays in ``[0, 1]``;
* reports are throttled: within one stage at most one per ``min_interval`` seconds, so a
  per-object tessellation loop does not post a message per object. A new stage, and the
  final ``1.0``, always go through;
* a failing callback never fails the conversion.
"""

from __future__ import annotations

import time
from typing import Callable

ProgressFn = Callable[[str, float], None]

#: At most this many reports per second within one stage (the page redraws no faster).
DEFAULT_MIN_INTERVAL = 0.1


class ConversionProgress:
    """Monotonic, throttled ``(stage, fraction)`` reporting over a host callback.

    ``callback`` may be ``None``: every method is then a no-op, so conversion code calls it
    unconditionally. ``detail`` (e.g. ``"12/40 objects"``) is appended to the stage text in
    parentheses; the throttle keys on the stage alone, so a changing count does not count
    as a new stage.
    """

    def __init__(
        self,
        callback: ProgressFn | None,
        *,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._callback = callback
        self._min_interval = float(min_interval)
        self._clock = clock
        self._fraction = 0.0
        self._stage: str | None = None
        self._last_post = float("-inf")

    @property
    def fraction(self) -> float:
        """The highest fraction reported so far."""
        return self._fraction

    def __call__(self, stage: str, fraction: float, detail: str | None = None) -> None:
        if self._callback is None:
            return
        try:
            f = float(fraction)
        except (TypeError, ValueError):
            f = self._fraction
        f = min(1.0, max(self._fraction, f))
        self._fraction = f
        now = self._clock()
        if stage == self._stage and f < 1.0 and now - self._last_post < self._min_interval:
            return
        self._stage = stage
        self._last_post = now
        text = f"{stage} ({detail})" if detail else stage
        try:
            self._callback(text, f)
        except Exception:  # noqa: BLE001 - progress must never fail a conversion
            pass

    def counter(self, stage: str, lo: float, hi: float, unit: str = "") -> Callable[[int, int], None]:
        """A ``(done, total)`` callback mapping ``done / total`` onto ``[lo, hi]`` under ``stage``,
        for loops that count (tessellation's :func:`~ada.visit.tessellate.report_tessellation_progress`)."""
        suffix = f" {unit}" if unit else ""

        def _counted(done: int, total: int) -> None:
            share = done / total if total else 1.0
            self(stage, lo + (hi - lo) * share, f"{done}/{total}{suffix}" if total else None)

        return _counted

    def labelled(self, stage: str, lo: float, hi: float) -> Callable[[int, int, str], None]:
        """A ``(done, total, label)`` callback onto ``[lo, hi]`` (the FEA bake's ``on_progress``)."""

        def _labelled(done: int, total: int, label: str = "") -> None:
            share = done / total if total else 1.0
            self(stage, lo + (hi - lo) * share, f"{label}, {done}/{total}" if label else f"{done}/{total}")

        return _labelled

    def scaled(self, lo: float, hi: float) -> ProgressFn:
        """A ``(stage, fraction)`` callback onto ``[lo, hi]`` (a writer reporting its own 0..1)."""

        def _scaled(stage: str, fraction: float) -> None:
            self(str(stage), lo + (hi - lo) * float(fraction or 0.0))

        return _scaled


def as_progress(on_progress) -> ConversionProgress:
    """``on_progress`` as a :class:`ConversionProgress` (one passed in is used as it is)."""
    if isinstance(on_progress, ConversionProgress):
        return on_progress
    return ConversionProgress(on_progress)
