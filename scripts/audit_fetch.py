"""Thin shim for ``ada audit fetch`` — download an audit conversion's source.

Kept so the ``pixi run audit-fetch`` task (and existing muscle memory) keep
working; the implementation now lives in :mod:`ada_cli.audit`.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from ada_cli.audit import cmd_fetch  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audit_id", type=int)
    ap.add_argument("--out", default="./audit_repro")
    ap.add_argument("--url", default=None)
    ap.add_argument("--token", default=None)
    raise SystemExit(cmd_fetch(ap.parse_args()))


if __name__ == "__main__":
    main()
