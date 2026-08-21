"""Compile a procedural doc to GLB, for the dev REST stub.

The stub cannot fake compiling — it produces a GLB from the model, and nothing static
stands in for that. But the compiler is plain Python and it is right here in the repo, so
the fixture shells out to it rather than answering 501. The result is the REAL compiler
output, not a stand-in: the same ``compile_doc`` the server worker calls.

Usage:  python dev-compile-procedural.py <doc.json> <out.glb> [--lod sim|detail]
                                         [--engine SLUG] [--name NAME]

Reads the document, writes the GLB, prints nothing on success. Any failure goes to stderr
and exits non-zero, so the caller can surface the real message instead of a generic 500.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Run against the working tree, not an installed copy — this is a dev fixture, and the
# point is to compile with the code you are editing.
REPO_SRC = Path(__file__).resolve().parents[3] / "src"
if REPO_SRC.is_dir():
    sys.path.insert(0, str(REPO_SRC))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("out")
    ap.add_argument("--lod", default="sim", choices=["sim", "detail"])
    ap.add_argument("--engine", default=None)
    ap.add_argument("--name", default="ProceduralModel")
    args = ap.parse_args()

    from ada.topo_model.wasm_compile import compile_doc

    doc = json.loads(Path(args.doc).read_text(encoding="utf-8"))
    # The client posts {"doc": {...}}; accept either that or a bare document, so this is
    # usable by hand as well as from the stub.
    if isinstance(doc, dict) and "doc" in doc and "spaces" not in doc:
        doc = doc["doc"]

    glb = compile_doc(doc, name=args.name, lod=args.lod, engine=args.engine)
    Path(args.out).write_bytes(glb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
