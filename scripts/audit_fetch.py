"""Download the source blob for a viewer audit row to ./audit_repro/<id>/.

Used to reproduce a failed conversion locally without spinning up a
viewer-api stack: pair with ``audit_repro.py`` for the round trip.

Auth: ``ADAPY_API_TOKEN`` (a CLI token minted from the admin panel).
Base URL: ``ADAPY_API_BASE`` *or* ``ADAPY_BASE_URL``
(e.g. ``https://viewer.example.com``).
"""

from __future__ import annotations

import argparse
import gzip
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
            f"error: {name} is unset. Mint a token from the admin panel " f"(CLI token button) and export it.\n"
        )
        sys.exit(2)
    return val


def _env_any(*names: str) -> str:
    """First non-empty match among ``names``. Lets the script accept
    either ``ADAPY_API_BASE`` or the more common ``ADAPY_BASE_URL`` —
    folks reach for whichever they have in their .env first."""
    for n in names:
        val = os.environ.get(n, "").strip()
        if val:
            return val
    sys.stderr.write(
        f"error: none of {', '.join(names)} are set. Set the viewer base URL "
        f"to (e.g.) https://viewer.example.com.\n"
    )
    sys.exit(2)


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


def _normalize_base(raw: str) -> str:
    raw = raw.rstrip("/")
    if "://" not in raw:
        # Default to https:// — the viewer is virtually always behind
        # TLS termination and copy-pasting just the hostname is the
        # common ergonomic mistake.
        raw = f"https://{raw}"
    return raw


def fetch(audit_id: int, dest_root: pathlib.Path) -> pathlib.Path:
    base = _normalize_base(_env_any("ADAPY_API_BASE", "ADAPY_BASE_URL"))
    token = _env("ADAPY_API_TOKEN")
    dest = dest_root / str(audit_id)
    dest.mkdir(parents=True, exist_ok=True)

    # Metadata first — gives us target_format and the error context to
    # save alongside the source so repro is self-contained.
    meta_bytes, _ = _api_get(f"{base}/api/admin/audit/{audit_id}", token)
    meta = json.loads(meta_bytes)
    (dest / "audit.json").write_text(json.dumps(meta, indent=2))

    # Source blob. The endpoint passes through any storage-layer
    # Content-Encoding (e.g. files stored gzipped land here with
    # `Content-Encoding: gzip`). Browsers decompress transparently;
    # urllib doesn't, so do it here so the on-disk file is always
    # the real source the converter will see — not a gzip wrapper.
    source_bytes, headers = _api_get(f"{base}/api/admin/audit/{audit_id}/source", token)
    if (headers.get("content-encoding") or "").lower() == "gzip":
        source_bytes = gzip.decompress(source_bytes)
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
        "--out",
        default="./audit_repro",
        help="Root directory for downloaded sources (default: ./audit_repro)",
    )
    args = ap.parse_args()

    dest_root = pathlib.Path(args.out)
    src = fetch(args.audit_id, dest_root)
    sys.stdout.write(f"{src}\n")


if __name__ == "__main__":
    main()
