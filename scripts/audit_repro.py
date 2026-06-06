"""Thin shim for ``ada audit repro`` — run an audit's conversion locally.

Downloads the source blob (idempotent) and invokes the converter directly so
a failing conversion's traceback lands on stderr in seconds. Kept so the
``pixi run audit-repro`` task keeps working; the implementation now lives in
:mod:`ada_cli.audit`.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from ada_cli.audit import cmd_repro  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audit_id", type=int)
    ap.add_argument("--out", default="./audit_repro")
    ap.add_argument("--target", default=None, help="Override the audit's target_format.")
    ap.add_argument("--url", default=None)
    ap.add_argument("--token", default=None)
    raise SystemExit(cmd_repro(ap.parse_args()))


if __name__ == "__main__":
    main()
