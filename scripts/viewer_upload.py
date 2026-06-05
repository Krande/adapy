"""Upload a local file (any kind) to the viewer as a source blob.

Mirrors what the SPA's drag-and-drop upload does, but driven from the
CLI so debug artifacts (intermediate GLBs, diagnostic STEP files, etc)
can be dropped into a scope without going through the browser.

Auth: ``ADAPY_API_TOKEN`` (a CLI token minted from the admin panel).
Base URL: ``ADAPY_API_BASE`` *or* ``ADAPY_BASE_URL``
(e.g. ``https://viewer.example.com``).

Examples:
    pixi run viewer-upload ./debug.glb
    pixi run viewer-upload ./debug.glb --as DEBUG_face_compare.glb
    pixi run viewer-upload ./debug.glb --scope user:me
    pixi run viewer-upload ./debug.glb --scope project:abc-123
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import pathlib
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
    """First non-empty match among ``names``. Accepts either ``ADAPY_API_BASE``
    or the more common ``ADAPY_BASE_URL`` — matching ``audit_fetch.py`` so folks
    can reach for whichever they already have in their .env."""
    for n in names:
        val = os.environ.get(n, "").strip()
        if val:
            return val
    sys.stderr.write(
        f"error: none of {', '.join(names)} are set. Set the viewer base URL "
        f"to (e.g.) https://viewer.example.com.\n"
    )
    sys.exit(2)


def _normalize_base(raw: str) -> str:
    raw = raw.rstrip("/")
    if "://" not in raw:
        # Default to https:// — the viewer is virtually always behind
        # TLS termination and copy-pasting just the hostname is the
        # common ergonomic mistake.
        raw = f"https://{raw}"
    return raw


# Content-type hints the viewer cares about. mimetypes guesses the
# rest from extension. GLB / IFC / STEP all need explicit entries
# because the system DB doesn't know them.
_EXTRA_TYPES = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".ifc": "application/x-ifc",
    ".stp": "application/step",
    ".step": "application/step",
    ".sat": "application/x-acis",
    ".xml": "application/xml",
    ".sif": "application/x-sesam-sif",
}


def _content_type_for(path: pathlib.Path) -> str:
    ext = path.suffix.lower()
    if ext in _EXTRA_TYPES:
        return _EXTRA_TYPES[ext]
    guess, _ = mimetypes.guess_type(path.name)
    return guess or "application/octet-stream"


def upload(local: pathlib.Path, scope: str, key: str) -> str:
    base = _normalize_base(_env_any("ADAPY_API_BASE", "ADAPY_BASE_URL"))
    token = _env("ADAPY_API_TOKEN")

    # Strip leading slashes; the {key:path} matcher accepts subdirs
    # but a leading slash trips the URL builder up.
    key = key.lstrip("/")
    if not key:
        sys.stderr.write("error: key is empty\n")
        sys.exit(2)

    body = local.read_bytes()
    url = f"{base}/api/scopes/{urllib.parse.quote(scope, safe=':')}" f"/blobs/{urllib.parse.quote(key)}"
    req = urllib.request.Request(
        url,
        data=body,
        method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": _content_type_for(local),
            "Content-Length": str(len(body)),
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            r.read()
            return url
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        sys.stderr.write(f"HTTP {e.code} on PUT {url}: {body}\n")
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=pathlib.Path, help="Local file to upload")
    ap.add_argument(
        "--as",
        dest="key",
        default=None,
        help="Remote key (default: local filename). Supports nested paths.",
    )
    ap.add_argument(
        "--scope",
        default="user:me",
        help="Target scope: shared, user:me, or project:<id> (default: user:me)",
    )
    args = ap.parse_args()

    if not args.path.exists():
        sys.stderr.write(f"error: {args.path} not found\n")
        sys.exit(2)
    if not args.path.is_file():
        sys.stderr.write(f"error: {args.path} is not a regular file\n")
        sys.exit(2)

    key = args.key or args.path.name
    size = args.path.stat().st_size
    sys.stdout.write(f"uploading {args.path} ({size / 1024 / 1024:.2f} MiB) → " f"scope={args.scope} key={key}\n")
    sys.stdout.flush()

    url = upload(args.path, args.scope, key)
    sys.stdout.write(f"ok: {url}\n")


# Late import — argparse usage strings shouldn't pay the urllib.parse
# import cost twice.
import urllib.parse  # noqa: E402

if __name__ == "__main__":
    main()
