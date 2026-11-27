"""A build made in the browser, handed to the server: the upload's framing and what core accepts.

A browser build (``ada.assets.wasm_build``) writes its GLB and summary in the viewer; the result
must still end up where every other reader looks for it -- shared storage, at the derived key core
composed -- because a build is a cache for everyone who can read the node, not for one tab.

THE SERVER DOES NOT TAKE THE BROWSER'S WORD FOR WHAT IT BUILT. The route that stores an upload
(``POST /assets/build/result``) composes the key and the fingerprint AGAIN, from the node's own
manifest, exactly as ``POST /assets/build`` does -- the caller names a node, never a key -- and
this module checks the upload against that request:

* the summary is a ``build@1`` document whose provenance names THIS request (provider, collection,
  subject, revision, node, fingerprint), and whose ``glb_key`` is a file of the upload, directly
  under the composed prefix;
* every file is one plain name under that prefix (no ``/``, no ``..``), of a kind a build writes
  (``.glb``, ``.json``), within a count and a size limit;
* the GLB is a glTF 2.0 binary whose header length is its size, whose JSON chunk parses, and whose
  ``asset.extras.provenance`` -- the copy a GLB carries of its own identity -- names the same
  request as the summary; ``glb_size``, when given, is its size;
* a ``.json`` file parses as JSON;
* an ``empty`` summary (nothing to draw) carries no files.

What this cannot check is that the triangles are the ones the provider's builder would have drawn:
re-deriving them is the build itself. That residual trust is governed, not assumed -- see the
route's docstring for who may upload and how an administrator turns browser builds off.

The FRAME is one request body, so one build is one round trip and no multipart parser is needed::

    <4 bytes: header length, little-endian>  <header: UTF-8 JSON>  <file 1> <file 2> ...

The header is ``{"request": {...}, "summary": {...}, "files": [{"name", "size",
"content_encoding"?}, ...]}``; the files follow in that order, back to back, and the body ends
exactly where the last one does.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from typing import Any, Mapping

from ada.assets.build import (
    BuildError,
    BuildSummary,
    parse_build_summary,
    read_glb_provenance,
    validate_build_provenance,
    validate_build_summary,
)

__all__ = [
    "ALLOWED_SUFFIXES",
    "MAX_FILES",
    "UploadedBuild",
    "UploadedFile",
    "check_uploaded_build",
    "encode_build_upload",
    "parse_build_upload",
]

#: The kinds of file a build writes beside its summary: the model, and JSON side documents.
ALLOWED_SUFFIXES = (".glb", ".json")
#: Most files one build may upload.
MAX_FILES = 16
#: The header is small: a summary and a file list. Anything bigger is not a build upload.
MAX_HEADER_BYTES = 4 * 1024 * 1024

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENCODINGS = (None, "gzip")


@dataclass(frozen=True)
class UploadedFile:
    name: str
    data: bytes
    content_encoding: str | None = None


@dataclass(frozen=True)
class UploadedBuild:
    request: dict
    summary: dict
    files: tuple[UploadedFile, ...]


def encode_build_upload(request: Mapping[str, Any], summary: Mapping[str, Any], files: list[UploadedFile]) -> bytes:
    """The frame :func:`parse_build_upload` reads (the viewer writes the same bytes; tests use this)."""
    header = {
        "request": dict(request),
        "summary": dict(summary),
        "files": [
            {
                "name": f.name,
                "size": len(f.data),
                **({"content_encoding": f.content_encoding} if f.content_encoding else {}),
            }
            for f in files
        ],
    }
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack("<I", len(raw)) + raw + b"".join(f.data for f in files)


def parse_build_upload(body: bytes) -> UploadedBuild:
    """Split a frame into its header and files; :class:`BuildError` for anything malformed."""
    if len(body) < 4:
        raise BuildError("the upload is empty: expected a framed build (header length, header, files)")
    (header_len,) = struct.unpack_from("<I", body, 0)
    if header_len <= 0 or header_len > MAX_HEADER_BYTES or 4 + header_len > len(body):
        raise BuildError(f"the upload's header length {header_len} does not fit the body of {len(body)} bytes")
    try:
        header = json.loads(body[4 : 4 + header_len].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BuildError(f"the upload's header is not JSON: {exc}") from exc
    if not isinstance(header, dict):
        raise BuildError("the upload's header must be a JSON object")
    request, summary, listing = header.get("request"), header.get("summary"), header.get("files") or []
    if not isinstance(request, dict) or not isinstance(summary, dict) or not isinstance(listing, list):
        raise BuildError("the upload's header needs 'request' and 'summary' objects and a 'files' list")
    if len(listing) > MAX_FILES:
        raise BuildError(f"a build uploads at most {MAX_FILES} files, this one {len(listing)}")
    files: list[UploadedFile] = []
    offset = 4 + header_len
    seen: set[str] = set()
    for entry in listing:
        if not isinstance(entry, dict):
            raise BuildError("every 'files' entry must be an object")
        name, size, encoding = entry.get("name"), entry.get("size"), entry.get("content_encoding")
        if not isinstance(name, str) or not _NAME.match(name) or ".." in name:
            raise BuildError(f"file name {name!r} is not one plain name (letters, digits, '.', '_', '-')")
        if not name.lower().endswith(ALLOWED_SUFFIXES):
            raise BuildError(f"file {name!r}: a build uploads {', '.join(ALLOWED_SUFFIXES)} files only")
        if name in seen:
            raise BuildError(f"file {name!r} is listed twice")
        seen.add(name)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise BuildError(f"file {name!r}: size {size!r} is not a byte count")
        if encoding not in _ENCODINGS:
            raise BuildError(f"file {name!r}: content_encoding {encoding!r} is not one of gzip or none")
        if offset + size > len(body):
            raise BuildError(f"file {name!r} runs past the end of the upload")
        files.append(UploadedFile(name=name, data=bytes(body[offset : offset + size]), content_encoding=encoding))
        offset += size
    if offset != len(body):
        raise BuildError(f"the upload carries {len(body) - offset} bytes the header does not list")
    return UploadedBuild(request=request, summary=summary, files=tuple(files))


def _check_glb(data: bytes, summary: BuildSummary) -> None:
    if len(data) < 20 or data[:4] != b"glTF":
        raise BuildError("the model is not a GLB: missing the glTF magic")
    version, total = struct.unpack_from("<II", data, 4)
    if version != 2:
        raise BuildError(f"the model is a GLB of container version {version}, not 2")
    if total != len(data):
        raise BuildError(f"the model's GLB header says {total} bytes, the file has {len(data)}")
    json_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != b"JSON" or 20 + json_len > len(data):
        raise BuildError("the model's first GLB chunk is not a JSON chunk that fits the file")
    try:
        json.loads(data[20 : 20 + json_len].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise BuildError(f"the model's GLB JSON chunk is unreadable: {exc}") from exc
    prov = read_glb_provenance(data)
    if not isinstance(prov, dict):
        raise BuildError("the model carries no provenance in asset.extras: a build must name what it built, twice")
    p = summary.provenance
    for name in ("provider", "collection", "subject", "revision", "node", "fingerprint"):
        if prov.get(name) != getattr(p, name):
            raise BuildError(
                f"the model's own provenance.{name} is {prov.get(name)!r}, the summary's {getattr(p, name)!r}"
            )
    if summary.glb_size is not None and summary.glb_size != len(data):
        raise BuildError(f"the summary's glb_size {summary.glb_size} is not the model's size {len(data)}")


def check_uploaded_build(
    upload: UploadedBuild,
    *,
    provider: str,
    collection: str,
    subject: str,
    revision: str,
    node: str | None,
    fingerprint: str,
    derived_prefix: str,
) -> BuildSummary:
    """Validate ``upload`` against the request core composed; answers the parsed summary.

    Raises :class:`BuildError` naming what disagrees (see the module docstring for the rules)."""
    summary = parse_build_summary(upload.summary)
    identity = dict(
        provider=provider, collection=collection, subject=subject, revision=revision, node=node, fingerprint=fingerprint
    )
    if summary.empty:
        validate_build_provenance(summary.provenance, **identity)
        if upload.files:
            raise BuildError("an empty build (nothing to draw) uploads no files")
        return summary
    validate_build_summary(summary, **identity, derived_prefix=derived_prefix)
    by_name = {f.name: f for f in upload.files}
    glb_name = summary.glb_key[len(derived_prefix) + 1 :]
    if glb_name not in by_name:
        raise BuildError(f"the summary's glb_key {summary.glb_key!r} is not a file of the upload ({sorted(by_name)})")
    if not glb_name.lower().endswith(".glb"):
        raise BuildError(f"the summary's glb_key {summary.glb_key!r} does not name a .glb file")
    for f in upload.files:
        if f.name.lower().endswith(".glb"):
            if f.name != glb_name:
                raise BuildError(f"file {f.name!r}: a build uploads one model, the summary's {glb_name!r}")
            _check_glb(f.data, summary)
        else:
            try:
                json.loads(f.data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError) as exc:
                raise BuildError(f"file {f.name!r} is not JSON: {exc}") from exc
    return summary
