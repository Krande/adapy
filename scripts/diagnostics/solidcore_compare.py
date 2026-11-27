"""EXPERIMENTAL: the solidcore prototype backend beside pythonocc (and ada-cpp) -- tubular-joint
accuracy and a few timings. Run in the `tests-solidcore` env after `pixi run build-solidcore`:

    pixi run -e tests-solidcore python scripts/diagnostics/solidcore_compare.py
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "compat"))

from test_solidcore_backend import (  # noqa: E402
    brace_beveled,
    brace_cut,
    brace_cut_reference_volume,
    occ_area,
    occ_volume,
)

from ada.cad import CadBackendName, backend_available, select_backend  # noqa: E402
from ada.geom.booleans import BoolOpEnum  # noqa: E402


def timed(fn, n: int) -> float:
    """Median wall time of fn() in milliseconds."""
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1e3)
    return statistics.median(out)


def main() -> None:
    names = [n for n in ("occ", "adacpp", "solidcore") if backend_available(CadBackendName(n))]
    bes = {n: select_backend(prefer=n) for n in names}
    print("backends:", ", ".join(names))

    print("\n== Tubular joint: brace TUB150x10 cut by chord TUB300x10 outer cylinder ==")
    for alpha in (90.0, 45.0):
        ref = brace_cut_reference_volume(alpha)
        print(f"\nalpha={alpha:.0f} deg   reference volume (quadrature) = {ref:.15e} m3")
        for n, be in bes.items():
            s = brace_cut(be, alpha)
            v = be.volume(s)
            line = f"  {n:9s} valid={be.is_valid(s)!s:5s} volume={v:.15e}  rel.err={(v - ref) / ref:+.2e}"
            if n == "occ":
                va = occ_volume(s)
                line += f"   (adaptive eps=1e-9: {va:.15e}, rel.err={(va - ref) / ref:+.2e}; area {occ_area(s):.12e})"
            elif n == "solidcore":
                line += f"   area {be.area(s):.12e}"
            print(line)
        for n in ("occ", "solidcore"):
            if n not in bes:
                continue
            be = bes[n]
            try:
                b = brace_beveled(be, alpha)
                vol_cut = occ_volume(brace_cut(be, alpha)) if n == "occ" else be.volume(brace_cut(be, alpha))
                vol_b = occ_volume(b) if n == "occ" else be.volume(b)
                print(
                    f"  {n:9s} 45deg single bevel (ruled-loft cutters): valid={be.is_valid(b)}, "
                    f"faces={len(be.faces(b))}, removed={vol_cut - vol_b:.12e} m3"
                )
            except Exception as e:  # noqa: BLE001 - a diagnostic: report and go on
                print(f"  {n:9s} bevel FAILED: {type(e).__name__}: {e}")

    print("\n== Timings (median ms) ==")
    ops = {
        "make_box": (lambda be: be.make_box(2.0, 3.0, 4.0), 200),
        "make_cylinder": (lambda be: be.make_cylinder(0.5, 10.0), 200),
        "box - cylinder": (
            lambda be: be.boolean(BoolOpEnum.DIFFERENCE, be.make_box(2.0, 3.0, 4.0), be.make_cylinder(0.5, 10.0)),
            50,
        ),
        "brace cut 90": (lambda be: brace_cut(be, 90.0), 10),
        "brace cut 45": (lambda be: brace_cut(be, 45.0), 10),
        "brace bevel 45 (2 lofts + 3 cuts)": (lambda be: brace_beveled(be, 45.0), 3),
    }
    header = f"{'operation':36s}" + "".join(f"{n:>12s}" for n in bes)
    print(header)
    for label, (fn, n) in ops.items():
        row = f"{label:36s}"
        for be in bes.values():
            try:
                row += f"{timed(lambda: fn(be), n):12.2f}"
            except Exception:  # noqa: BLE001
                row += f"{'n/a':>12s}"
        print(row)
    shapes = {n: brace_cut(be, 45.0) for n, be in bes.items()}
    for label, fn, n in (
        ("volume(brace cut 45)", lambda be, s: be.volume(s), 20),
        ("is_valid(brace cut 45)", lambda be, s: be.is_valid(s), 20),
        # default density on every kernel. NOT an explicit 1 mm: OccBackend.tessellate reads its
        # argument as adapy's OCC "quality", and 0.001 there runs the process out of memory.
        ("tessellate(brace cut 45, default)", lambda be, s: be.tessellate(s), 10),
    ):
        row = f"{label:36s}"
        for name, be in bes.items():
            try:
                row += f"{timed(lambda: fn(be, shapes[name]), n):12.2f}"
            except Exception:  # noqa: BLE001
                row += f"{'n/a':>12s}"
        print(row)
    if "occ" in bes:
        print(f"{'occ volume, adaptive eps=1e-9':36s}{timed(lambda: occ_volume(shapes['occ']), 20):12.2f}")
    print(f"\n(pid {os.getpid()})")


if __name__ == "__main__":
    main()
