"""Download the source blob for a viewer audit row to ./audit_repro/<id>/.

Used to reproduce a failed conversion locally without spinning up a
viewer-api stack: pair with ``audit_repro.py`` for the round trip.

Auth: ``ADAPY_API_TOKEN`` (a CLI token minted from the admin panel).
Base URL: ``ADAPY_API_BASE`` (e.g. ``https://viewer.example.com``).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request


def _env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        sys.stderr.write(
            f"error: {name} is unset. Mint a token from the admin panel "
            f"(CLI token button) and export it.\n"
        )
        sys.exit(2)
    return val


def _api_get(url: str, token: str) -> tuple[bytes, dict[str, str]]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"HTTP {e.code} on {url}: {body}\n")
        sys.exit(1)


def _filename_from_disposition(value: str | None) -> str | None:
    if not value:
        return None
    m = re.search(r'filename="([^"]+)"', value)
    return m.group(1) if m else None


def fetch(audit_id: int, dest_root: pathlib.Path) -> pathlib.Path:
    base = _env("ADAPY_API_BASE").rstrip("/")
    token = _env("ADAPY_API_TOKEN")
    dest = dest_root / str(audit_id)
    dest.mkdir(parents=True, exist_ok=True)

    # Metadata first — gives us target_format and the error context to
    # save alongside the source so repro is self-contained.
    meta_bytes, _ = _api_get(f"{base}/api/admin/audit/{audit_id}", token)
    meta = json.loads(meta_bytes)
    (dest / "audit.json").write_text(json.dumps(meta, indent=2))

    # Source blob.
    source_bytes, headers = _api_get(
        f"{base}/api/admin/audit/{audit_id}/source", token
    )
    name = (
        _filename_from_disposition(headers.get("content-disposition"))
        or (meta.get("key") or "").rsplit("/", 1)[-1]
        or f"audit-{audit_id}.bin"
    )
    src = dest / name
    src.write_bytes(source_bytes)
    return src


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audit_id", type=int)
    ap.add_argument(
        "--out", default="./audit_repro",
        help="Root directory for downloaded sources (default: ./audit_repro)",
    )
    args = ap.parse_args()

    dest_root = pathlib.Path(args.out)
    src = fetch(args.audit_id, dest_root)
    sys.stdout.write(f"{src}\n")


if __name__ == "__main__":
    main()
