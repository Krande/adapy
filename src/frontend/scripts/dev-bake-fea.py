"""Bake FEA streaming artefacts from a source file, for the dev REST stub.

The stub could serve the *pre-baked* fixture and nothing else, so an uploaded `.sin` or
`.rmed` landed in storage and then had nothing to stream from — the manifest request 404'd
and Results mode stayed empty.

Baking is not something a fixture can fake, but it is not something it has to: the bake is
plain Python and it is in this repo. This drives the same
``bake_fea_artefacts_from_source`` the server worker calls, so what the viewer streams is
the real artefact set — mesh GLB, per-field blobs, manifest — not a stand-in.

Usage:  python dev-bake-fea.py <source> <out_dir> [--key KEY]

Prints nothing on success. Failures go to stderr and exit non-zero so the caller can
surface the real reason.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The working tree, not an installed copy — a dev fixture should bake with the code you
# are editing.
REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if REPO_SRC.is_dir():
    sys.path.insert(0, str(REPO_SRC))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("out_dir")
    ap.add_argument("--key", default="")
    args = ap.parse_args()

    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bake_fea_artefacts_from_source(Path(args.source), out, src_key=args.key or Path(args.source).stem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
