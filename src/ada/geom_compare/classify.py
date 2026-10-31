"""Pass / uncertain / fail for one element seen by two geometry sources.

THE RULE, cheapest first:

1. **missing** -- the element is on one side only. Reported, never a pass: either side may simply
   not model it (a source that leaves insulation out), so it is its own verdict, not a fail.
2. **pass** -- every box face within ``box_tol_mm`` AND the volumes within ``vol_tol`` (relative), or
   the volume not comparable because a mesh is not closed (box only).
3. **fail** -- a box face off by more than ``fail_mm``, or (both closed) the volumes off by more than
   ``vol_fail``.
4. Otherwise the cheap measures cannot decide (a box off by a chord, a volume a few percent out), and
   the SURFACES are compared (``distance.surface_distance``): sampled Hausdorff within
   ``dist_tol_mm`` is a **pass** (the difference is tessellation), beyond ``fail_mm`` a **fail**, in
   between **uncertain**. Only these pairs pay for triangles and sampling.

Every verdict carries the metrics that decided it and one sentence saying why.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from .summary import ElementSummary

__all__ = ["FAIL", "MISSING", "PASS", "UNCERTAIN", "VERDICTS", "PairResult", "Thresholds", "classify", "needs_surfaces"]

PASS, UNCERTAIN, FAIL, MISSING = "pass", "uncertain", "fail", "missing"
VERDICTS = (PASS, UNCERTAIN, FAIL, MISSING)


@dataclass(frozen=True)
class Thresholds:
    box_tol_mm: float = 1.0
    vol_tol: float = 0.005
    fail_mm: float = 25.0
    vol_fail: float = 0.10
    dist_tol_mm: float = 2.0

    @classmethod
    def from_options(cls, options: dict | None) -> "Thresholds":
        """From a run's settings: known keys only, positive numbers only."""
        values = {}
        for name in cls.__dataclass_fields__:
            raw = (options or {}).get(name)
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                values[name] = value
        return cls(**values)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PairResult:
    verdict: str
    reason: str
    metrics: dict = field(default_factory=dict)


def _box_delta(a: ElementSummary, b: ElementSummary) -> float:
    return max(
        max(abs(x - y) for x, y in zip(a.bbox_min, b.bbox_min)),
        max(abs(x - y) for x, y in zip(a.bbox_max, b.bbox_max)),
    )


def _box_volume(s: ElementSummary) -> float:
    d = [max(0.0, hi - lo) for lo, hi in zip(s.bbox_min, s.bbox_max)]
    return d[0] * d[1] * d[2]


def _open(s: ElementSummary) -> bool:
    """No meaningful volume: a signed volume near zero or negative against a non-flat box, or one
    claiming more than its own box."""
    v = s.volume
    box = _box_volume(s)
    return v > box * 1.05 + 1.0 or (box > 1.0 and v < box * 1e-6)


def cheap_metrics(a: ElementSummary, b: ElementSummary) -> dict:
    a_open, b_open = _open(a), _open(b)
    vol_ratio = a.volume / b.volume if (not a_open and not b_open and b.volume) else None
    return {
        "box_delta_mm": _box_delta(a, b),
        "volume_ratio": vol_ratio,
        "area_ratio": a.area / b.area if b.area else None,
        "triangle_ratio": a.triangles / b.triangles if b.triangles else None,
        "open": [a_open, b_open],
    }


def needs_surfaces(a: Optional[ElementSummary], b: Optional[ElementSummary], t: Thresholds) -> bool:
    """Whether the cheap measures leave this pair undecided (step 4)."""
    if a is None or b is None:
        return False
    m = cheap_metrics(a, b)
    return _cheap_verdict(m, t) is None


def _cheap_verdict(m: dict, t: Thresholds) -> Optional[PairResult]:
    box, ratio = m["box_delta_mm"], m["volume_ratio"]
    vol_ok = ratio is None or abs(ratio - 1.0) <= t.vol_tol
    if box <= t.box_tol_mm and vol_ok:
        why = "box and volume agree" if ratio is not None else "box agrees (a mesh is not closed: volume not compared)"
        return PairResult(PASS, why, m)
    if box > t.fail_mm:
        return PairResult(FAIL, f"box off by {box:.1f} mm", m)
    if ratio is not None and abs(ratio - 1.0) > t.vol_fail:
        return PairResult(FAIL, f"volume x{ratio:.3f}", m)
    return None


def classify(
    a: Optional[ElementSummary],
    b: Optional[ElementSummary],
    t: Thresholds,
    surfaces: Optional[Callable[[], object]] = None,
) -> PairResult:
    """The verdict on one pair. ``surfaces`` returns a ``distance.SurfaceDistance`` in mm for the pair
    (called only when the cheap measures cannot decide; None: those pairs stay uncertain)."""
    if a is None or b is None:
        side = "first" if a is None else "second"
        return PairResult(MISSING, f"only in the {'second' if side == 'first' else 'first'} source", {"missing": side})
    m = cheap_metrics(a, b)
    decided = _cheap_verdict(m, t)
    if decided is not None:
        return decided
    if surfaces is None:
        return PairResult(UNCERTAIN, f"box off by {m['box_delta_mm']:.1f} mm; surfaces not compared", m)
    try:
        d = surfaces()
    except Exception as exc:  # noqa: BLE001 - the pair is undecided, and says why
        return PairResult(UNCERTAIN, f"surfaces could not be compared ({type(exc).__name__}: {exc})", m)
    m = {**m, "hausdorff_mm": d.hausdorff, "p95_mm": d.p95, "mean_mm": d.mean, "spacing_mm": d.spacing}
    if d.hausdorff <= t.dist_tol_mm:
        return PairResult(PASS, f"surfaces within {d.hausdorff:.2f} mm (tessellation)", m)
    if d.hausdorff > t.fail_mm:
        return PairResult(FAIL, f"surfaces up to {d.hausdorff:.1f} mm apart", m)
    return PairResult(UNCERTAIN, f"surfaces up to {d.hausdorff:.1f} mm apart (p95 {d.p95:.1f} mm)", m)
