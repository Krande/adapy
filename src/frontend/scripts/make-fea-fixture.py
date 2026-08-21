"""Bake the FEA fixture the dev server loads with ``?fea=1``.

Results mode cannot be reviewed against an empty scene: the step/mode scrubber, the
colour legend, the field picker and the data table all need a real result deck. This
bakes one from a source already in the repo — the code_aster eigen cantilever, chosen
because an eigenvalue result has SEVERAL MODES, which is what makes the two-slider
scrubber worth looking at. A static result would give one step and prove nothing.

Run (from the repo root):

    .pixi/envs/fem/python.exe src/frontend/scripts/make-fea-fixture.py

Writes src/frontend/public/dev/fea/ — the same manifest + sidecar tree the viewer
streams in production, so the fixture exercises the real load path
(load_fea_streaming.ts → feaFetcher → applyField), not a shortcut.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ada.fem.results.artefacts import bake_fea_artefacts_from_source  # noqa: E402

# Shell elements give a mesh with visible surface deformation; the line variant is too
# sparse to read as a mode shape, and the solid variant is heavier for no extra benefit.
SOURCE = REPO_ROOT / "files/fem_files/cantilever/code_aster/eigen_shell_cantilever_code_aster.rmed"
OUT = Path(__file__).resolve().parent.parent / "public" / "dev" / "fea"


def main() -> int:
    if not SOURCE.exists():
        print(f"source not found: {SOURCE}", file=sys.stderr)
        return 1

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)

    result = bake_fea_artefacts_from_source(SOURCE, OUT, src_key="dev-cantilever")

    total = 0
    for path in sorted(OUT.rglob("*")):
        if path.is_file():
            size = path.stat().st_size
            total += size
            print(f"  {path.relative_to(OUT).as_posix():<40} {size / 1024:8.1f} kB")

    print(f"\nbaked {SOURCE.name} -> {OUT.relative_to(REPO_ROOT).as_posix()}  ({total / 1024:.0f} kB total)")
    print(f"manifest: {result.manifest_path.name}")
    if total > 8 * 1024 * 1024:
        print("WARNING: fixture is large; keep the repo light.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
