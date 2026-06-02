"""Multi-file analysis bundles, packaged as ``.zip`` for upload.

Abaqus jobs commonly span multiple `.inp` files glued together with
``*INCLUDE, INPUT=...`` directives. Single-file upload doesn't fit;
zipping the deck and uploading one archive does. The same pattern
applies to Sesam multi-deck setups (Genie XML, FEM) — but the initial
implementation handles Abaqus only and rejects other families with a
clear error rather than silently misbehaving.

Two responsibilities live here:

* :func:`inspect_bundle` — pure structure check. Given an extracted
  bundle directory, decide which family it is, locate the entry-point,
  and validate that every referenced file exists and uses a portable
  relative path. Returns a :class:`BundleInfo` or raises
  :class:`BundleError` with a human-readable reason.

* :func:`unpack_bundle` — copy a zip's contents to a fresh tempdir
  with safety checks (no path traversal, no symlinks, no absolute
  members).

The conversion path itself lives in :mod:`converter` and uses both.
"""

from __future__ import annotations

import io
import pathlib
import re
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Iterable


class BundleError(ValueError):
    """Raised for any user-visible problem with an uploaded bundle.

    Strings are intended to surface to the operator via the audit log /
    convert job error field, so they describe the *bundle* rather than
    internal state."""


# Family detection: each non-trivial file must belong to exactly one
# family. Auxiliary extensions (`.dat`, `.par`) are weak indicators —
# they match any family if at least one strong indicator is present.
_ABAQUS_STRONG: frozenset[str] = frozenset({".inp"})
_ABAQUS_AUX: frozenset[str] = frozenset({".dat", ".par", ".inc"})
_GENIE_STRONG: frozenset[str] = frozenset({".xml"})
_SESAM_STRONG: frozenset[str] = frozenset({".fem"})

# Files we ignore inside a bundle — editor cruft, OS metadata, etc.
# Keeping the predicate explicit (instead of a blanket "non-data
# extensions") avoids the case where a real `.inp` is mis-classified.
_IGNORE_NAMES: frozenset[str] = frozenset({".DS_Store", "Thumbs.db"})


@dataclass(frozen=True)
class BundleInfo:
    """Outcome of :func:`inspect_bundle`."""

    family: str  # 'abaqus' for now; future: 'genie', 'sesam_fem', ...
    entry: pathlib.Path  # absolute path to the entry-point file
    referenced: tuple[pathlib.Path, ...]  # all files reachable from entry


def _ext(name: str) -> str:
    return pathlib.PurePosixPath(name).suffix.lower()


def _list_data_files(root: pathlib.Path) -> list[pathlib.Path]:
    """All files under ``root`` (recursive), minus ignored cruft."""
    out: list[pathlib.Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.name in _IGNORE_NAMES or p.name.startswith("._"):
            continue
        out.append(p)
    return out


def _classify_family(files: Iterable[pathlib.Path]) -> str:
    """Decide which analysis family a flat file list belongs to.

    Raises :class:`BundleError` on mixed or unknown families. ``.inc``
    and similar auxiliary extensions are treated as Abaqus-compatible
    when at least one ``.inp`` is present; without a strong indicator
    they have no family of their own.
    """
    exts = {_ext(p.name) for p in files}
    has_abaqus = bool(exts & _ABAQUS_STRONG)
    has_genie = bool(exts & _GENIE_STRONG)
    has_sesam = bool(exts & _SESAM_STRONG)

    families = [
        name
        for name, present in (
            ("abaqus", has_abaqus),
            ("genie", has_genie),
            ("sesam_fem", has_sesam),
        )
        if present
    ]

    if len(families) > 1:
        raise BundleError(
            f"bundle mixes formats: {', '.join(sorted(exts))}. "
            "A bundle must contain files for one analysis stack only."
        )
    if not families:
        raise BundleError(f"bundle contains no recognised source files (saw {sorted(exts)})")

    fam = families[0]
    # Reject any extension that doesn't fit the chosen family — a stray
    # .ifc in an Abaqus bundle is a smell worth surfacing rather than
    # silently dropping.
    if fam == "abaqus":
        allowed = _ABAQUS_STRONG | _ABAQUS_AUX
    elif fam == "genie":
        allowed = _GENIE_STRONG | _ABAQUS_AUX  # aux often fine
    else:
        allowed = _SESAM_STRONG | _ABAQUS_AUX

    stray = exts - allowed
    if stray:
        raise BundleError(f"bundle contains files that don't belong to the {fam} stack: " f"{sorted(stray)}")

    if fam != "abaqus":
        # Initial scope: Abaqus only. Don't pretend to support the
        # others until the entry-point detection logic actually exists
        # for them.
        raise BundleError(f"only Abaqus bundles are supported; detected: {fam}")
    return fam


# `*INCLUDE, INPUT=foo.inp` (with optional whitespace, case-insensitive,
# value may be quoted). Comments start with `**`. We don't try to handle
# Abaqus' line-continuation comma — `*INCLUDE` directives almost never
# wrap. If a real-world deck breaks this, we'll add continuation
# support then.
_INCLUDE_RE = re.compile(
    r"^\s*\*INCLUDE\b[^\n*]*?\bINPUT\s*=\s*([\"']?)([^\s,\"']+)\1",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_includes(content: str) -> list[str]:
    """Extract ``INPUT=...`` filenames from an Abaqus deck.

    Skips comment lines (``**`` prefix). Returns a list of raw filename
    strings — caller decides whether to reject them as non-portable.
    """
    out: list[str] = []
    # Strip out comment lines so a `**` describing an *INCLUDE doesn't
    # match. Faster than building a comment-aware regex.
    lines = [ln for ln in content.splitlines() if not ln.lstrip().startswith("**")]
    cleaned = "\n".join(lines)
    for m in _INCLUDE_RE.finditer(cleaned):
        out.append(m.group(2).strip())
    return out


def _read_text_lenient(p: pathlib.Path) -> str:
    """Decode an Abaqus deck as text. Encodings vary across exporters
    (Windows-1252 from Abaqus/CAE, UTF-8 from hand-edits) so we try a
    couple of common options before giving up."""
    raw = p.read_bytes()
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _validate_relative(filename: str) -> str:
    """Reject path forms we won't support inside a bundle.

    * absolute paths (``/foo/bar``, ``C:\\bar``) — not portable across
      machines, and we want bundles to be self-contained.
    * ``..`` segments — would let an INCLUDE escape the temp dir.
    * backslash separators — Abaqus on Windows accepts them, but
      mixing backslashes into a tarball is fragile; reject.
    """
    if not filename:
        raise BundleError("empty INCLUDE filename")
    if "\\" in filename:
        raise BundleError(
            f"INCLUDE filename {filename!r} uses backslashes; " "use forward slashes so the bundle is portable"
        )
    p = pathlib.PurePosixPath(filename)
    if p.is_absolute() or (len(filename) > 1 and filename[1] == ":"):
        raise BundleError(
            f"INCLUDE filename {filename!r} is absolute; " "bundles must use paths relative to the entry-point"
        )
    if any(part == ".." for part in p.parts):
        raise BundleError(f"INCLUDE filename {filename!r} traverses outside the bundle")
    return filename


def _abaqus_entry(root: pathlib.Path, files: list[pathlib.Path]) -> tuple[pathlib.Path, tuple[pathlib.Path, ...]]:
    """Pick the Abaqus entry-point and walk its include chain.

    Entry-point rule: the unique ``.inp`` that no other ``.inp``
    INCLUDEs. Ambiguous (zero or two+ candidates) → :class:`BundleError`.
    """
    inp_files = [p for p in files if _ext(p.name) == ".inp"]
    if not inp_files:
        raise BundleError("Abaqus bundle has no .inp file")

    # Map relative-from-root → absolute, so INCLUDE strings (which are
    # relative) can be resolved without guessing.
    by_rel: dict[str, pathlib.Path] = {str(p.relative_to(root)).replace("\\", "/"): p for p in files}

    # Build the "is included by another" set. INCLUDE filenames are
    # relative to the file that contains the directive, so resolve
    # against each .inp's own directory.
    included: set[pathlib.Path] = set()
    for src in inp_files:
        text = _read_text_lenient(src)
        for raw in _parse_includes(text):
            _validate_relative(raw)
            rel = str((src.parent / raw).resolve().relative_to(root.resolve())).replace("\\", "/")
            target = by_rel.get(rel)
            if target is None:
                raise BundleError(f"include not found: {raw!r} (referenced by " f"{src.relative_to(root)})")
            included.add(target.resolve())

    candidates = [p for p in inp_files if p.resolve() not in included]
    if len(candidates) == 0:
        raise BundleError(
            "no entry-point: every .inp is included by another. "
            "Bundle needs a top-level deck that nothing references."
        )
    if len(candidates) > 1:
        rels = sorted(str(p.relative_to(root)) for p in candidates)
        raise BundleError(
            f"ambiguous entry-point: {len(candidates)} top-level .inp "
            f"files ({', '.join(rels)}). Bundle should have exactly one."
        )

    entry = candidates[0]
    # Collect the transitive set so callers can audit / log it.
    referenced = _walk_includes(entry, root, by_rel)
    return entry, tuple(sorted(referenced))


def _walk_includes(
    entry: pathlib.Path,
    root: pathlib.Path,
    by_rel: dict[str, pathlib.Path],
) -> set[pathlib.Path]:
    """BFS the include chain from ``entry``. Validates each reference."""
    seen: set[pathlib.Path] = {entry}
    stack: list[pathlib.Path] = [entry]
    while stack:
        cur = stack.pop()
        text = _read_text_lenient(cur)
        for raw in _parse_includes(text):
            _validate_relative(raw)
            rel = str((cur.parent / raw).resolve().relative_to(root.resolve())).replace("\\", "/")
            target = by_rel.get(rel)
            if target is None:
                raise BundleError(f"include not found: {raw!r} (referenced by " f"{cur.relative_to(root)})")
            if target.resolve() not in seen:
                seen.add(target.resolve())
                stack.append(target)
    return seen


def inspect_bundle(root: pathlib.Path) -> BundleInfo:
    """Validate an extracted bundle and return its entry-point info.

    Caller is responsible for unpacking the zip safely first (see
    :func:`unpack_bundle`); this function only inspects on-disk state.
    """
    files = _list_data_files(root)
    if not files:
        raise BundleError("bundle is empty")
    family = _classify_family(files)
    if family == "abaqus":
        entry, referenced = _abaqus_entry(root, files)
        return BundleInfo(family=family, entry=entry, referenced=referenced)
    raise BundleError(f"unsupported family: {family}")


def unpack_bundle(zip_bytes: bytes, dest: pathlib.Path) -> None:
    """Extract a zip archive into ``dest`` with traversal protection.

    Rejects absolute member names, ``..`` segments, and symlinks
    outright — the unpacked tree must live entirely under ``dest`` so
    a malicious archive can't write to (or read from) elsewhere.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as exc:
        raise BundleError(f"not a valid zip archive: {exc}") from exc

    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()

    for info in zf.infolist():
        name = info.filename
        if not name or name.endswith("/"):
            continue  # directories — created implicitly by writes
        if name.startswith("/") or "\\" in name:
            raise BundleError(f"zip member {name!r} uses absolute or windows path")
        # ZipInfo.create_system + external_attr expose unix mode bits;
        # the symlink bit is 0xA in the high nibble.
        mode = (info.external_attr >> 16) & 0xF000
        if mode == 0xA000:
            raise BundleError(f"zip member {name!r} is a symlink (rejected)")

        target = (dest / name).resolve()
        try:
            target.relative_to(dest_resolved)
        except ValueError:
            raise BundleError(f"zip member {name!r} escapes bundle root")

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, open(target, "wb") as out:
            out.write(src.read())


def unpack_and_inspect(zip_bytes: bytes) -> tuple[tempfile.TemporaryDirectory, BundleInfo]:
    """Convenience: unpack to a fresh temp dir + inspect, returning both.

    The TemporaryDirectory must be kept alive until the caller is done
    reading files (its ``__exit__`` removes the tree). Most callers
    should use a ``with`` block.
    """
    tmp = tempfile.TemporaryDirectory(prefix="ada-bundle-")
    try:
        unpack_bundle(zip_bytes, pathlib.Path(tmp.name))
        info = inspect_bundle(pathlib.Path(tmp.name))
    except BaseException:
        tmp.cleanup()
        raise
    return tmp, info
