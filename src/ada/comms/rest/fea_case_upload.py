"""Validation of a load-combination case computed in the browser and uploaded.

A viewer that materialises a lazy load combination itself (the adacpp_fea wasm
engine) may send it back to the server's case cache, so the next viewer gets
it as a plain 200 from ``GET .../fea/case``. It arrives one file at a time
through ``POST /scopes/{scope}/fea/artefact`` with ``name`` =
``cases/<n>-<recipeHash8>/<file>``: the case's single-step blobs first, its
``fea.case.json`` overlay last.

Nothing here trusts the client beyond the bytes it computed. Every file is
checked against the FRESH base manifest the route reads:

* the path: ``cases/<n>-<hash8>/fea.*``, where ``n`` is a Tier-A combination of
  the manifest (not ``needs_raw``) and ``hash8`` its recipe hash's first eight
  hex digits -- a recipe that changed since the client read the manifest is
  refused;
* a blob: one of the filenames the manifest gives the case fields' base blobs,
  exactly ``header_bytes + stride_bytes`` long (one step), with the right magic
  and a header that says one step of that stride;
* the overlay: JSON of the lazy-case contract (``kind`` ``fea_case``, the
  manifest's ``bake_version``, this case's ``n`` and full recipe hash), naming
  only blobs of the case, each already stored. ``producer`` is stamped by the
  server (``uploaded: true``) whatever the client sent.

Pure Python (no numpy): the API image is slim.
"""

from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass

#: ``cases/<n>-<recipeHash8>/<file>`` -- the only nested name the per-file
#: upload route accepts.
CASE_FILE_RE = re.compile(r"^cases/(?P<n>\d{1,12})-(?P<hash8>[0-9a-f]{8})/(?P<file>fea\.[A-Za-z0-9_.+-]{1,200})$")

CASE_OVERLAY_NAME = "fea.case.json"

#: The overlay is a few KB per field; anything near this is not one.
MAX_CASE_OVERLAY_BYTES = 4 * 1024 * 1024

_HEADER_MAGICS = {b"AFBL", b"AFEL"}
_HEADER_FIXED = 12  # magic + uint32 version + uint32 json length


class CaseUploadError(ValueError):
    """The upload is refused; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class CaseFile:
    n: int
    hash8: str
    file: str

    @property
    def case_dir(self) -> str:
        return f"{self.n}-{self.hash8}"

    @property
    def is_overlay(self) -> bool:
        return self.file == CASE_OVERLAY_NAME


def parse_case_file_name(name: str) -> CaseFile | None:
    """The parts of a ``cases/<n>-<hash8>/fea.*`` name, or None for any other name."""
    m = CASE_FILE_RE.match(name or "")
    if not m or ".." in m.group("file"):
        return None
    return CaseFile(int(m.group("n")), m.group("hash8"), m.group("file"))


def combination_entry(manifest: dict, cf: CaseFile) -> dict:
    """The manifest's combination ``cf`` belongs to, or a CaseUploadError."""
    entry = next((e for e in manifest.get("combination_steps") or [] if int(e.get("n", -1)) == cf.n), None)
    if entry is None or not entry.get("recipe_hash"):
        raise CaseUploadError(404, f"case {cf.n} is not a lazy combination of this bake")
    if str(entry["recipe_hash"])[:8] != cf.hash8:
        raise CaseUploadError(
            409, f"case {cf.n}: recipe {cf.hash8} is not the bake's ({str(entry['recipe_hash'])[:8]})"
        )
    if entry.get("needs_raw"):
        raise CaseUploadError(422, f"case {cf.n} needs the raw records; only the server can materialise it")
    return entry


def case_blob_layouts(manifest: dict) -> dict[str, dict]:
    """``{blob filename: blob dict}`` of every blob a case can carry: the base
    blobs of the fields a case has values for (``combine.case_fields``)."""
    out: dict[str, dict] = {}
    for f in manifest.get("fields") or []:
        if f.get("category") == "property" or not (f.get("steps") or []):
            continue
        blobs = [pt.get("blob") for pt in f.get("per_type") or []] if f.get("per_type") else [f.get("blob")]
        for b in blobs:
            if b and b.get("url"):
                out[str(b["url"])] = b
    return out


def validate_case_blob(manifest: dict, cf: CaseFile, data: bytes) -> None:
    """A single-step case blob of the right layout, or a CaseUploadError."""
    layouts = case_blob_layouts(manifest)
    blob = layouts.get(cf.file)
    if blob is None:
        raise CaseUploadError(400, f"{cf.file} is not a blob of a case field of this bake")
    header_bytes = int(blob.get("header_bytes") or 0)
    stride = int(blob.get("stride_bytes") or 0)
    if len(data) != header_bytes + stride:
        raise CaseUploadError(400, f"{cf.file}: {len(data)} bytes, a one-step blob is {header_bytes + stride}")
    if len(data) < _HEADER_FIXED or data[:4] not in _HEADER_MAGICS:
        raise CaseUploadError(400, f"{cf.file}: not an AFBL/AFEL blob")
    _version, json_len = struct.unpack_from("<II", data, 4)
    if _HEADER_FIXED + json_len > header_bytes:
        raise CaseUploadError(400, f"{cf.file}: header overruns its {header_bytes} bytes")
    try:
        header = json.loads(data[_HEADER_FIXED : _HEADER_FIXED + json_len].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaseUploadError(400, f"{cf.file}: unreadable header") from exc
    if int(header.get("n_steps", -1)) != 1 or int(header.get("stride_bytes", -1)) != stride:
        raise CaseUploadError(400, f"{cf.file}: header is not one step of {stride} bytes")


def validate_case_overlay(manifest: dict, cf: CaseFile, entry: dict, data: bytes) -> tuple[dict, list[str]]:
    """The overlay to store (producer stamped) and the blob filenames it names,
    or a CaseUploadError."""
    if len(data) > MAX_CASE_OVERLAY_BYTES:
        raise CaseUploadError(413, f"{CASE_OVERLAY_NAME} exceeds {MAX_CASE_OVERLAY_BYTES} bytes")
    try:
        overlay = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaseUploadError(400, f"{CASE_OVERLAY_NAME} is not JSON") from exc
    if not isinstance(overlay, dict) or overlay.get("kind") != "fea_case":
        raise CaseUploadError(400, f"{CASE_OVERLAY_NAME}: not a fea_case overlay")
    if overlay.get("bake_version") != manifest.get("bake_version"):
        raise CaseUploadError(
            409, f"{CASE_OVERLAY_NAME}: bake_version {overlay.get('bake_version')} != {manifest.get('bake_version')}"
        )
    case = overlay.get("case") or {}
    if not isinstance(case, dict) or case.get("n") != cf.n or case.get("recipe_hash") != entry.get("recipe_hash"):
        raise CaseUploadError(409, f"{CASE_OVERLAY_NAME}: not case {cf.n} with the bake's recipe")
    layouts = case_blob_layouts(manifest)
    names: list[str] = []
    for field in overlay.get("fields") or []:
        if not isinstance(field, dict):
            raise CaseUploadError(400, f"{CASE_OVERLAY_NAME}: malformed field")
        buckets = field.get("per_type") if field.get("per_type") else [field]
        for b in buckets:
            url = str(((b or {}).get("blob") or {}).get("url") or "")
            if url not in layouts:
                raise CaseUploadError(400, f"{CASE_OVERLAY_NAME}: names {url!r}, not a case blob of this bake")
            names.append(url)
    if not names:
        raise CaseUploadError(400, f"{CASE_OVERLAY_NAME}: no fields")
    producer = overlay.get("producer") if isinstance(overlay.get("producer"), dict) else {}
    overlay["producer"] = {
        "engine": str(producer.get("engine") or "unknown")[:64],
        "version": str(producer.get("version") or "unknown")[:64],
        "tier": str(producer.get("tier") or "A")[:16],
        "uploaded": True,
    }
    overlay.pop("prefix", None)
    return overlay, sorted(set(names))
