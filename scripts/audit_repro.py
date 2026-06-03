"""Reproduce a failed viewer conversion locally without going through
the REST API or queue.

Downloads the source blob via :mod:`audit_fetch` (idempotent — skips
the network call when the file already exists) and invokes
:func:`ada.comms.rest.converter.convert` directly with the original
``target_format``. The traceback lands on stderr in seconds, which is
the whole point — fast iteration on a failing conversion without a
running viewer-api stack.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import traceback

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import audit_fetch  # noqa: E402


def _ensure_local(audit_id: int, root: pathlib.Path) -> tuple[pathlib.Path, dict]:
    dest = root / str(audit_id)
    meta_path = dest / "audit.json"
    if meta_path.exists() and any(p for p in dest.iterdir() if p.name != "audit.json"):
        meta = json.loads(meta_path.read_text())
        # Pick the source file by matching the key tail; fall back to
        # any non-metadata file in the dir.
        key_tail = (meta.get("key") or "").rsplit("/", 1)[-1]
        candidate = dest / key_tail if key_tail else None
        if candidate and candidate.exists():
            return candidate, meta
        for p in dest.iterdir():
            if p.name != "audit.json":
                return p, meta
    src = audit_fetch.fetch(audit_id, root)
    meta = json.loads((dest / "audit.json").read_text())
    return src, meta


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audit_id", type=int)
    ap.add_argument(
        "--out",
        default="./audit_repro",
        help="Root directory for downloaded sources (default: ./audit_repro)",
    )
    ap.add_argument(
        "--target",
        default=None,
        help="Override the target_format from the audit row.",
    )
    args = ap.parse_args()

    root = pathlib.Path(args.out)
    src, meta = _ensure_local(args.audit_id, root)
    target = args.target or meta.get("target_format") or "glb"

    # Imported lazily so plain `audit-fetch` doesn't pay the
    # import-time cost of the converter (and its FEM stack).
    from ada.comms.rest.converter import convert

    sys.stdout.write(f"repro audit_id={args.audit_id} src={src} target={target}\n")
    sys.stdout.write(f"original status={meta.get('status')} error={meta.get('error')!r}\n")
    sys.stdout.flush()

    def on_progress(stage: str, frac: float) -> None:
        sys.stdout.write(f"  [{frac:5.1%}] {stage}\n")
        sys.stdout.flush()

    try:
        out = convert(src, str(src), target, on_progress)
    except Exception:
        sys.stderr.write("\n--- repro raised ---\n")
        traceback.print_exc()
        sys.exit(1)

    out_path = src.with_suffix(f".out.{target.lstrip('.')}")
    out_path.write_bytes(out)
    sys.stdout.write(f"ok: wrote {out_path} ({len(out)} bytes)\n")


if __name__ == "__main__":
    main()
