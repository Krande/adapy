"""``ada files`` — list, download, and upload blobs in a scope.

All commands talk to adapy-viewer via the same ``ADAPY_VIEWER_URL`` /
``ADAPY_VIEWER_TOKEN`` env pair the existing ``ada build upload`` uses;
the scope is supplied per invocation (``--scope project:<slug>`` or
``ADAPY_VIEWER_SCOPE``) since cli/bot tokens can address multiple
scopes.

``download`` and ``upload`` try the presigned-URL flow first so large
files go S3-direct without pinning an API worker for the duration of
the transfer. Local-storage deployments respond 503 to the presign
request, in which case we fall back to streaming through the
``/blobs/{key}`` API route (subject to its direct-upload size cap).
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

# 30-minute ceiling matches ada-build's upload timeout; both directions
# can take a while on modest connections for sub-GB artefacts.
_TRANSFER_TIMEOUT_SECONDS = 30 * 60


def _config(args: argparse.Namespace) -> tuple[str, str, str]:
    base_url = (getattr(args, "url", None) or os.environ.get("ADAPY_VIEWER_URL", "")).strip().rstrip("/")
    token = (getattr(args, "token", None) or os.environ.get("ADAPY_VIEWER_TOKEN", "")).strip()
    scope = (getattr(args, "scope", None) or os.environ.get("ADAPY_VIEWER_SCOPE", "")).strip()
    missing = []
    if not base_url:
        missing.append("ADAPY_VIEWER_URL (or --url)")
    if not token:
        missing.append("ADAPY_VIEWER_TOKEN (or --token)")
    if not scope:
        missing.append("--scope (or ADAPY_VIEWER_SCOPE), e.g. project:my-slug")
    if missing:
        for m in missing:
            print(f"missing: {m}", file=sys.stderr)
        sys.exit(2)
    return base_url, token, scope


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def cmd_list(args: argparse.Namespace) -> int:
    import httpx

    base_url, token, scope = _config(args)
    url = f"{base_url}/api/scopes/{scope}/files"
    with httpx.Client(timeout=60) as client:
        resp = client.get(url, headers=_auth(token))
    if resp.status_code >= 400:
        print(f"list failed: {resp.status_code} {resp.text}", file=sys.stderr)
        return 1
    files = resp.json().get("files", [])
    if args.prefix:
        files = [f for f in files if f["key"].startswith(args.prefix)]
    files.sort(key=lambda f: f["key"])
    if args.long:
        for f in files:
            print(f"{f['size']:>12}  {f['key']}")
    else:
        for f in files:
            print(f["key"])
    if not files:
        print("(no files)", file=sys.stderr)
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    import httpx

    base_url, token, scope = _config(args)
    key = args.key.strip().lstrip("/")
    dest = pathlib.Path(args.dest) if args.dest else pathlib.Path(key.rsplit("/", 1)[-1])
    if dest.is_dir():
        dest = dest / key.rsplit("/", 1)[-1]
    if dest.parent and str(dest.parent) not in ("", "."):
        dest.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=_TRANSFER_TIMEOUT_SECONDS) as client:
        if args.via_api:
            return _download_via_api(client, base_url, token, scope, key, dest)

        url_resp = client.post(
            f"{base_url}/api/scopes/{scope}/download-url",
            json={"key": key},
            headers={**_auth(token), "Content-Type": "application/json"},
        )
        if url_resp.status_code == 503:
            # Local-storage backend can't presign — fall through.
            return _download_via_api(client, base_url, token, scope, key, dest)
        if url_resp.status_code == 404:
            print(f"not found: {key}", file=sys.stderr)
            return 1
        if url_resp.status_code >= 400:
            print(
                f"download-url failed: {url_resp.status_code} {url_resp.text}",
                file=sys.stderr,
            )
            return 1
        payload = url_resp.json()
        signed_url = payload["url"]
        expected = payload.get("size")
        return _download_via_url(client, signed_url, dest, expected)


def _download_via_url(
    client,
    url: str,
    dest: pathlib.Path,
    expected_size: int | None,
) -> int:
    # No Authorization on the signed URL — SigV4 only signs a fixed
    # header set, and an unsigned Authorization breaks the signature
    # check ("SignatureDoesNotMatch").
    with client.stream("GET", url) as resp:
        if resp.status_code >= 400:
            body = resp.read()
            print(
                f"presigned GET failed: {resp.status_code} {body[:200]!r}",
                file=sys.stderr,
            )
            return 1
        _write_stream(resp, dest)
    _print_done(dest, expected_size)
    return 0


def _download_via_api(
    client,
    base_url: str,
    token: str,
    scope: str,
    key: str,
    dest: pathlib.Path,
) -> int:
    url = f"{base_url}/api/scopes/{scope}/blobs/{key}"
    with client.stream("GET", url, headers=_auth(token)) as resp:
        if resp.status_code == 404:
            print(f"not found: {key}", file=sys.stderr)
            return 1
        if resp.status_code >= 400:
            body = resp.read()
            print(
                f"download failed: {resp.status_code} {body[:200]!r}",
                file=sys.stderr,
            )
            return 1
        _write_stream(resp, dest)
    _print_done(dest, None)
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    import httpx

    base_url, token, scope = _config(args)
    src = pathlib.Path(args.src)
    if not src.is_file():
        print(f"not a file: {src}", file=sys.stderr)
        return 2
    key = (args.key or src.name).strip().lstrip("/")
    size = src.stat().st_size

    with httpx.Client(timeout=_TRANSFER_TIMEOUT_SECONDS) as client:
        if not args.via_api:
            url_resp = client.post(
                f"{base_url}/api/scopes/{scope}/upload-url",
                json={"key": key},
                headers={**_auth(token), "Content-Type": "application/json"},
            )
            if url_resp.status_code != 503:  # 503 = local backend can't presign; fall through
                if url_resp.status_code >= 400:
                    print(
                        f"upload-url failed: {url_resp.status_code} {url_resp.text}",
                        file=sys.stderr,
                    )
                    return 1
                # No Authorization on the signed URL (same SigV4 constraint as download).
                with src.open("rb") as fh:
                    put = client.put(
                        url_resp.json()["url"],
                        content=fh,
                        headers={"Content-Length": str(size)},
                    )
                if put.status_code >= 400:
                    print(
                        f"presigned PUT failed: {put.status_code} {put.text[:200]}",
                        file=sys.stderr,
                    )
                    return 1
                # Finalise: audit row + auto-conversion enqueue happen server-side here.
                done = client.post(
                    f"{base_url}/api/scopes/{scope}/upload-complete",
                    json={"key": key},
                    headers={**_auth(token), "Content-Type": "application/json"},
                )
                if done.status_code >= 400:
                    print(
                        f"upload-complete failed: {done.status_code} {done.text}",
                        file=sys.stderr,
                    )
                    return 1
                print(f"  uploaded {src} -> {scope}/{key} ({done.json().get('size', size)} bytes)")
                return 0

        with src.open("rb") as fh:
            resp = client.put(
                f"{base_url}/api/scopes/{scope}/blobs/{key}",
                content=fh,
                headers={**_auth(token), "Content-Length": str(size)},
            )
        if resp.status_code >= 400:
            print(f"upload failed: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
            return 1
        print(f"  uploaded {src} -> {scope}/{key} ({size} bytes)")
        return 0


def _write_stream(resp, dest: pathlib.Path) -> None:
    with dest.open("wb") as fh:
        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
            fh.write(chunk)


def _print_done(dest: pathlib.Path, expected_size: int | None) -> None:
    size = dest.stat().st_size
    if expected_size is not None and size != expected_size:
        print(
            f"  warning: wrote {size} bytes, expected {expected_size}",
            file=sys.stderr,
        )
    print(f"  wrote {dest} ({size} bytes)")
