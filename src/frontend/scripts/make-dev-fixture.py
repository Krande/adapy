"""Generate the dev fixture model the vite dev server loads with ``?demo=1``.

Without this, ``npm run dev`` boots to an empty canvas (there is no bundled sample
model and the WS backend isn't running), which makes every UI review start with a
blank viewport. The fixture is a small, deliberately *varied* frame -- beams with
different sections, plates, and a nested part hierarchy -- so the outliner, the
properties panel, selection highlighting and the section-plane tools all have
something real to act on.

Run (from the repo root):

    .pixi/envs/tests/python.exe src/frontend/scripts/make-dev-fixture.py

Writes src/frontend/public/dev/demo.glb. Regenerate whenever the fixture needs to
cover a new panel; it is committed so a fresh clone can `npm run dev` immediately.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

import ada  # noqa: E402


def build() -> ada.Assembly:
    """A two-storey braced frame: enough hierarchy and section variety to exercise the UI."""
    bay, storey = 6.0, 4.0

    columns = ada.Part("Columns")
    for i, (x, y) in enumerate([(0, 0), (bay, 0), (bay, bay), (0, bay)]):
        for lvl in range(2):
            z0, z1 = lvl * storey, (lvl + 1) * storey
            columns / ada.Beam(f"COL{i + 1}_L{lvl + 1}", (x, y, z0), (x, y, z1), "HE200B")

    beams = ada.Part("Beams")
    ring = [(0, 0), (bay, 0), (bay, bay), (0, bay)]
    for lvl in range(1, 3):
        z = lvl * storey
        for i in range(4):
            x0, y0 = ring[i]
            x1, y1 = ring[(i + 1) % 4]
            beams / ada.Beam(f"BM{lvl}_{i + 1}", (x0, y0, z), (x1, y1, z), "IPE300")

    braces = ada.Part("Braces")
    braces / ada.Beam("BR1", (0, 0, 0), (bay, 0, storey), "TUB200x10")
    braces / ada.Beam("BR2", (bay, bay, 0), (0, bay, storey), "TUB200x10")

    plates = ada.Part("Plates")
    for lvl in range(1, 3):
        z = lvl * storey
        plates / ada.Plate(
            f"DECK_L{lvl}",
            [(0, 0), (bay, 0), (bay, bay), (0, bay)],
            0.02,
            origin=(0, 0, z),
            normal=(0, 0, 1),
            xdir=(1, 0, 0),
        )

    topside = ada.Part("Topside") / (columns, beams, braces, plates)
    return ada.Assembly("DevFixture") / topside


def main() -> int:
    out = Path(__file__).resolve().parent.parent / "public" / "dev" / "demo.glb"
    out.parent.mkdir(parents=True, exist_ok=True)

    asm = build()
    asm.to_gltf(out)

    size_kb = out.stat().st_size / 1024
    print(f"wrote {out.relative_to(REPO_ROOT)} ({size_kb:.0f} kB)")
    if size_kb > 4096:
        print("WARNING: fixture is large; keep it small so the repo stays light.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
