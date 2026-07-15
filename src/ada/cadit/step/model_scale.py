"""Estimate a model reference scale (bbox diagonal, world units) for adaptive tessellation.

The adaptive angular density (see ``ada.cad.registry.stream_tess_adaptive``) relaxes the fine
angular ceiling for curved surfaces small *relative to the model*. That needs a model length
scale. A raw min/max bounding box is unusable: CAD STEP files routinely carry a handful of
far-flung reference/construction points (the large reference assembly spans ±500 m of stray points yet its real
geometry is ~13-45 m), which inflate the diagonal ~30x and would coarsen every surface.

So we use an OUTLIER-ROBUST estimate: sample CARTESIAN_POINT coordinates and take ~2x the 99th
percentile of point magnitude. The scan is bounded (a prefix of the file is enough to capture the
bulk distribution; the rare outliers are exactly what we want the percentile to reject), so this
adds only a short read even on a multi-GB STEP.
"""

from __future__ import annotations

import pathlib
import re

_COORD_RE = re.compile(rb"CARTESIAN_POINT\([^(]*\(\s*([-0-9.eE]+)\s*,\s*([-0-9.eE]+)\s*,\s*([-0-9.eE]+)")


def estimate_step_model_scale(
    path: str | pathlib.Path, *, budget_bytes: int = 96_000_000, chunks: int = 24, max_samples: int = 600_000
) -> float:
    """Robust model bbox-diagonal estimate for a STEP file, or 0.0 if it can't be determined.

    Samples ``chunks`` evenly-spaced windows across the whole file (CARTESIAN_POINT entities may sit
    anywhere — some exporters emit them only in the last ~40% of the file, so a prefix scan misses
    them), collecting up to ``max_samples`` point magnitudes within ``budget_bytes`` of total reads.
    Returns ``2 * p99(|point|)`` — the p99 rejects stray reference points, doubling turns a radius
    into a diameter-like span. 0.0 (adaptive disabled for this file) when too few points are found.
    """
    try:
        import numpy as np

        p = pathlib.Path(path)
        size = p.stat().st_size
    except OSError:
        return 0.0

    n_chunks = max(1, chunks)
    win = max(1, budget_bytes // n_chunks)
    mags: list[float] = []
    with open(p, "rb") as f:
        for i in range(n_chunks):
            off = 0 if n_chunks == 1 else int((size - win) * i / (n_chunks - 1))
            f.seek(max(0, off))
            data = f.read(win)
            for m in _COORD_RE.finditer(data):
                try:
                    x, y, z = float(m.group(1)), float(m.group(2)), float(m.group(3))
                except ValueError:
                    continue
                mags.append((x * x + y * y + z * z) ** 0.5)
            if len(mags) >= max_samples:
                break

    if len(mags) < 100:
        return 0.0
    return float(2.0 * np.percentile(np.asarray(mags), 99.0))
