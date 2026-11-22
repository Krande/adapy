"""Robot-asset bundles: the robot assets of the provider store, exported to and imported from one
zip file (``.adabots``). See ``docs/architecture/robot_asset_bundles.md`` for the format and the rules.

A **robot asset** is provider data (``docs/design/provider-scope-grants.md``): one folder per version in
the provider store, ``assets/robots/<id>/<version>/``, holding a ``robot.json`` and the files it names
(meshes at one or more levels of detail, anything else the asset carries). A version without its
``robot.json`` is incomplete and is not used. ``assets/robots/index.json`` lists the complete versions,
because a scope reads the store's files by key through its grant and never lists them.

This module is the format and the store side, free of FastAPI: the listing, the streaming export, the
validation of an uploaded bundle, the preview of what an import would do and the import itself.
``routes/admin_robot_assets.py`` puts it on ``/api/admin/robot-assets``.

No step keeps a local copy of a bundle or of a file in it: the upload streams into the staging
object, the validation and the import read that object in place (:func:`open_staged`), and each
file of an imported robot streams from the zip into its own object. The local disk a bundle needs
is none, whatever its size; memory is a few blocks and parts (``Storage.open_seekable``,
``Storage.put_stream``).
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import io
import json
import os
import pathlib
import re
import stat
import zipfile
import zlib
from dataclasses import dataclass, field
from typing import IO, Any, AsyncIterator, Iterable, Optional, Union

from .scope import Scope

# --- identity -------------------------------------------------------------------------------------

#: The bundle's manifest schema. A reader refuses another major (``@2``).
BUNDLE_SCHEMA = "ada.robots/bundle@1"
BUNDLE_SCHEMA_FAMILY = "ada.robots/bundle@"
BUNDLE_FORMAT_VERSION = 1
#: The file suffix of a bundle. A zip by content: ``unzip`` opens it.
BUNDLE_SUFFIX = ".adabots"
#: What an export is served as. A zip, so a browser or a mail filter treats it as one.
BUNDLE_MEDIA_TYPE = "application/zip"
#: The manifest's name, at the root of the zip.
BUNDLE_MANIFEST = "manifest.json"
#: Where each robot asset version sits in a bundle: ``robots/<id>/<version>/<path>``.
BUNDLE_ROBOTS_DIR = "robots"

#: The provider and collection the robot assets are published as, and granted to scopes by.
PROVIDER_ID = "robot-assets"
COLLECTION = "robots"
#: Where they are in the provider store.
STORE_PREFIX = "assets/robots"
MANIFEST_NAME = "robot.json"
INDEX_KEY = STORE_PREFIX + "/index.json"
#: The index's schema, as the robot-simulation plugin that lists stored robots reads it. An index
#: already in the store keeps its own ``schema``.
INDEX_SCHEMA = "robot-sim/robot-asset-index/1"
#: Where an uploaded bundle waits between its preview and its import (in the provider store, so any
#: API replica can finish what another previewed).
STAGING_PREFIX = "_staging/robot-bundles"
#: A staged bundle older than this is removed at the next preview.
STAGING_TTL_S = 24 * 3600

ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]|_(?!_))*$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
#: A file's path inside an asset version: relative, forward slashes, no dot segments.
PATH_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.@-]*)*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")

#: The file types a bundle may carry (lower-case suffix). Anything else refuses the bundle.
ALLOWED_SUFFIXES = frozenset(
    {
        ".json",
        ".stl",
        ".dae",
        ".glb",
        ".gltf",
        ".bin",
        ".obj",
        ".mtl",
        ".urdf",
        ".xacro",
        ".xml",
        ".yaml",
        ".yml",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".ktx2",
        ".txt",
        ".md",
    }
)

#: Fixed zip metadata, so the same assets give the same bytes.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
_ZIP_FILE_MODE = (stat.S_IFREG | 0o644) << 16
_ZIP_LEVEL = 6
_CHUNK = 1 << 20
#: A member up to this size is written with one PUT; a larger one is streamed up as a multipart
#: upload of parts this size (S3's smallest part but the last is 5 MiB).
_PART = 5 << 20

#: A bundle as the validation and the import read it: a local path, or an open, seekable binary
#: file (the staged object, read in place: :func:`open_staged`).
BundleSource = Union[pathlib.Path, str, IO[bytes]]


class BundleError(Exception):
    """A bundle that is refused as a whole. ``problems`` says every reason found."""

    def __init__(self, message: str, problems: Optional[list[str]] = None) -> None:
        super().__init__(message)
        self.problems = problems or [message]


# --- limits ---------------------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Limits:
    """What an uploaded bundle may be. Each is an environment variable (``ADA_ROBOT_BUNDLE_*``)."""

    max_bundle_bytes: int = 1 << 30  # the upload itself
    max_total_bytes: int = 4 << 30  # every file unpacked
    max_file_bytes: int = 512 << 20  # one file unpacked
    max_files: int = 10_000
    max_robots: int = 1_000
    max_manifest_bytes: int = 16 << 20  # manifest.json, and each robot.json
    max_ratio: int = 1_000  # unpacked / packed, for a file over 1 MiB

    @classmethod
    def from_env(cls) -> "Limits":
        d = cls()
        return cls(
            max_bundle_bytes=_env_int("ADA_ROBOT_BUNDLE_MAX_BYTES", d.max_bundle_bytes),
            max_total_bytes=_env_int("ADA_ROBOT_BUNDLE_MAX_TOTAL_BYTES", d.max_total_bytes),
            max_file_bytes=_env_int("ADA_ROBOT_BUNDLE_MAX_FILE_BYTES", d.max_file_bytes),
            max_files=_env_int("ADA_ROBOT_BUNDLE_MAX_FILES", d.max_files),
            max_robots=_env_int("ADA_ROBOT_BUNDLE_MAX_ROBOTS", d.max_robots),
            max_manifest_bytes=d.max_manifest_bytes,
            max_ratio=d.max_ratio,
        )

    def as_dict(self) -> dict:
        return {
            "max_bundle_bytes": self.max_bundle_bytes,
            "max_total_bytes": self.max_total_bytes,
            "max_file_bytes": self.max_file_bytes,
            "max_files": self.max_files,
            "max_robots": self.max_robots,
        }


# --- versions -------------------------------------------------------------------------------------

_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
_TOKEN_RE = re.compile(r"\d+|[A-Za-z]+")


def version_scheme(version: str) -> str:
    """``revision`` (an integer: ``1``, ``2``, ``10``), ``semver`` (``1.4.0``, ``2.0.0-rc1``) or
    ``natural`` (anything else, ordered by its numbers and words)."""
    if version.isdigit():
        return "revision"
    if _SEMVER_RE.match(version):
        return "semver"
    return "natural"


def _tokens(text: str) -> tuple:
    return tuple((0, int(t), "") if t.isdigit() else (1, 0, t.lower()) for t in _TOKEN_RE.findall(text))


def version_key(version: str) -> tuple:
    """The sort key of a version: numbers compare as numbers (``2`` < ``10``, ``1.9.0`` < ``1.10.0``),
    a semver pre-release comes before its release (``1.0.0-rc1`` < ``1.0.0``), and the plain string
    breaks a tie so the order is total."""
    m = _SEMVER_RE.match(version)
    if m:
        core = tuple((0, int(m.group(i)), "") for i in (1, 2, 3))
        pre = m.group(4)
        return (core, 0 if pre else 1, _tokens(pre or ""), version)
    return (_tokens(version), 1, (), version)


def latest_version(versions: Iterable[str]) -> Optional[str]:
    vs = list(versions)
    return max(vs, key=version_key) if vs else None


# --- hashing --------------------------------------------------------------------------------------


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_sha256(files: Iterable[tuple[str, str]]) -> str:
    """One hash of a whole asset version: sha256 over ``"<sha256>  <path>\\n"`` per file, sorted by
    path (the lines ``sha256sum`` prints). Two versions with the same files byte for byte have the
    same content hash, wherever they are stored and however (gzipped at rest or not)."""
    lines = "".join(f"{sha}  {path}\n" for path, sha in sorted(files))
    return sha256_bytes(lines.encode("utf-8"))


def asset_ref(robot_id: str, version: str) -> str:
    return f"{robot_id}@{version}"


def asset_prefix(robot_id: str, version: str) -> str:
    return f"{STORE_PREFIX}/{robot_id}/{version}/"


def parse_ref(ref: str) -> tuple[str, Optional[str]]:
    """``id@version`` -> ``(id, version)``; a bare ``id`` -> ``(id, None)`` (every version)."""
    robot_id, sep, version = ref.strip().partition("@")
    if not ID_RE.match(robot_id) or (sep and not VERSION_RE.match(version)):
        raise ValueError(f"{ref!r} is not a robot asset ref (<id>@<version>, or <id> for every version)")
    return robot_id, (version if sep else None)


def suffix_allowed(path: str) -> bool:
    return pathlib.PurePosixPath(path).suffix.lower() in ALLOWED_SUFFIXES


def _pins(doc: Any) -> list[tuple[str, str]]:
    """Every ``{"path": ..., "sha256": ...}`` a robot.json names, anywhere in it."""
    out: list[tuple[str, str]] = []
    if isinstance(doc, dict):
        p, s = doc.get("path"), doc.get("sha256")
        if isinstance(p, str) and isinstance(s, str):
            out.append((p, s))
        for v in doc.values():
            out.extend(_pins(v))
    elif isinstance(doc, list):
        for v in doc:
            out.extend(_pins(v))
    return out


def robot_metadata(doc: dict) -> dict:
    """What a list or a preview shows of a robot.json: the fields people know it by, where it has them."""
    meta: dict = {}
    for name in ("schema", "label", "manufacturer", "model", "roles", "payload_kg", "reach_m"):
        if name in doc:
            meta[name] = doc[name]
    levels = (doc.get("meshes") or {}).get("levels") if isinstance(doc.get("meshes"), dict) else None
    if isinstance(levels, dict):
        meta["levels"] = sorted(levels)
    return meta


# --- the store ------------------------------------------------------------------------------------

STORE = Scope.providers()


@dataclass
class StoredVersion:
    robot_id: str
    version: str
    files: dict[str, int] = field(default_factory=dict)  # path -> size as stored
    last_modified: Optional[str] = None
    manifest: Optional[dict] = None
    manifest_sha256: Optional[str] = None
    problem: Optional[str] = None

    @property
    def ref(self) -> str:
        return asset_ref(self.robot_id, self.version)

    @property
    def complete(self) -> bool:
        return MANIFEST_NAME in self.files

    @property
    def prefix(self) -> str:
        return asset_prefix(self.robot_id, self.version)


async def list_store(storage, *, read_manifests: bool = True) -> dict[str, dict[str, StoredVersion]]:
    """``{id: {version: StoredVersion}}`` of what is under ``assets/robots/`` in the provider store,
    complete or not. Keys that are not ``<id>/<version>/<path>`` (the index) are left out."""
    out: dict[str, dict[str, StoredVersion]] = {}
    for entry in await storage.list_prefix(STORE, STORE_PREFIX + "/"):
        rest = entry.key[len(STORE_PREFIX) + 1 :] if entry.key.startswith(STORE_PREFIX + "/") else ""
        parts = rest.split("/", 2)
        if len(parts) != 3 or not all(parts) or not ID_RE.match(parts[0]) or not VERSION_RE.match(parts[1]):
            continue
        sv = out.setdefault(parts[0], {}).setdefault(parts[1], StoredVersion(parts[0], parts[1]))
        sv.files[parts[2]] = int(entry.size)
        if entry.last_modified and (sv.last_modified is None or entry.last_modified > sv.last_modified):
            sv.last_modified = entry.last_modified
    if read_manifests:
        for versions in out.values():
            for sv in versions.values():
                if sv.complete:
                    await _read_manifest(storage, sv)
    return out


async def _read_manifest(storage, sv: StoredVersion) -> None:
    try:
        raw = await storage.get_bytes(STORE, sv.prefix + MANIFEST_NAME)
        sv.manifest_sha256 = sha256_bytes(raw)
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
        sv.manifest = doc
    except Exception as exc:  # noqa: BLE001 - an unreadable manifest is shown, not fatal to the list
        sv.problem = f"{MANIFEST_NAME} could not be read: {exc}"


def complete_versions(store: dict[str, dict[str, StoredVersion]], robot_id: str) -> dict[str, StoredVersion]:
    return {v: sv for v, sv in store.get(robot_id, {}).items() if sv.complete}


def listing(store: dict[str, dict[str, StoredVersion]]) -> list[dict]:
    """The admin list: one row per robot id, its complete versions newest first, and its latest."""
    rows = []
    for robot_id in sorted(store):
        versions = complete_versions(store, robot_id)
        if not versions:
            continue
        ordered = sorted(versions.values(), key=lambda sv: version_key(sv.version), reverse=True)
        latest = ordered[0]
        meta_latest = robot_metadata(latest.manifest or {})
        rows.append(
            {
                "id": robot_id,
                "name": meta_latest.get("label") or robot_id,
                "latest": latest.version,
                "versions": [
                    {
                        "ref": sv.ref,
                        "version": sv.version,
                        "version_scheme": version_scheme(sv.version),
                        "latest": sv is latest,
                        "files": len(sv.files),
                        "bytes": sum(sv.files.values()),
                        "manifest_sha256": sv.manifest_sha256,
                        "last_modified": sv.last_modified,
                        "metadata": robot_metadata(sv.manifest or {}),
                        "problem": sv.problem,
                    }
                    for sv in ordered
                ],
            }
        )
    return rows


def select(store: dict[str, dict[str, StoredVersion]], refs: Optional[list[str]]) -> list[StoredVersion]:
    """The complete versions ``refs`` name (``id@version``, or ``id`` for all of its versions); None
    for every complete version in the store. Ordered by id, then version. Unknown refs raise
    ``KeyError`` naming them."""
    chosen: dict[str, StoredVersion] = {}
    missing: list[str] = []
    if refs is None:
        for robot_id in store:
            for sv in complete_versions(store, robot_id).values():
                chosen[sv.ref] = sv
    else:
        for ref in refs:
            robot_id, version = parse_ref(ref)
            versions = complete_versions(store, robot_id)
            if version is None:
                if not versions:
                    missing.append(ref)
                for sv in versions.values():
                    chosen[sv.ref] = sv
            elif version in versions:
                chosen[ref] = versions[version]
            else:
                missing.append(ref)
    if missing:
        raise KeyError(", ".join(missing))
    return sorted(chosen.values(), key=lambda sv: (sv.robot_id, version_key(sv.version)))


async def _logical_chunks(storage, key: str) -> AsyncIterator[bytes]:
    """A stored file's own bytes, a chunk at a time: gzip at rest undone on the fly."""
    res = await storage.open_stream(STORE, key)
    decomp = zlib.decompressobj(zlib.MAX_WBITS | 16) if res.content_encoding == "gzip" else None
    async for chunk in res.stream:
        if decomp is not None:
            out = decomp.decompress(chunk)
            if out:
                yield out
        elif chunk:
            yield chunk
    if decomp is not None:
        tail = decomp.flush()
        if tail:
            yield tail


async def stored_content(storage, sv: StoredVersion) -> tuple[str, dict[str, str]]:
    """``(content_sha256, {path: sha256})`` of a stored version, read file by file."""
    hashes: dict[str, str] = {}
    for path in sorted(sv.files):
        h = hashlib.sha256()
        async for chunk in _logical_chunks(storage, sv.prefix + path):
            h.update(chunk)
        hashes[path] = h.hexdigest()
    return content_sha256(hashes.items()), hashes


# --- export ---------------------------------------------------------------------------------------


class _Sink(io.RawIOBase):
    """A write-only, unseekable stream for :class:`zipfile.ZipFile` that hands back what was written:
    the zip is produced as it is written, never held whole."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []
        self._pos = 0

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, b) -> int:  # type: ignore[override]
        data = bytes(b)
        self._parts.append(data)
        self._pos += len(data)
        return len(data)

    def tell(self) -> int:
        return self._pos

    def seek(self, *_a, **_k):  # noqa: D401 - zipfile probes this and falls back to data descriptors
        raise OSError("unseekable")

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        out = b"".join(self._parts)
        self._parts.clear()
        return out


def _zinfo(name: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=_ZIP_EPOCH)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.create_system = 3
    zi.external_attr = _ZIP_FILE_MODE
    return zi


def exported_at_now() -> str:
    """The export time: now (UTC, seconds), or ``SOURCE_DATE_EPOCH`` where it is set, for a
    reproducible build of the same bytes."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if epoch.isdigit():
        ts = _dt.datetime.fromtimestamp(int(epoch), tz=_dt.timezone.utc)
    else:
        ts = _dt.datetime.now(tz=_dt.timezone.utc)
    return ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def bundle_filename(chosen: list[StoredVersion], exported_at: str) -> str:
    if len(chosen) == 1:
        return f"{chosen[0].robot_id}@{chosen[0].version}{BUNDLE_SUFFIX}"
    day = exported_at[:10].replace("-", "")
    return f"robots-{day}-{len(chosen)}{BUNDLE_SUFFIX}"


def _adapy_version() -> str:
    try:
        from ada._version import __version__

        return str(__version__)
    except Exception:  # noqa: BLE001 - a build without a version still exports
        return "unknown"


def dump_json(doc: Any) -> bytes:
    """Canonical JSON: sorted keys, one-space indent, UTF-8, a final newline."""
    return (json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


async def export_stream(
    storage,
    chosen: list[StoredVersion],
    store: dict[str, dict[str, StoredVersion]],
    *,
    exported_at: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """The bundle of ``chosen``, as chunks of a zip. Each file is read from the store a chunk at a
    time and compressed into the zip as it comes, so memory holds a chunk, not the catalogue. The
    entries are sorted and carry fixed times and modes; ``manifest.json`` comes last because it
    holds every file's sha256, which is known once the file has been written."""
    exported_at = exported_at or exported_at_now()
    sink = _Sink()
    zf = zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=_ZIP_LEVEL)
    robots = []
    for sv in chosen:
        latest = latest_version(complete_versions(store, sv.robot_id))
        files = []
        skipped = []
        for path in sorted(sv.files):
            if not PATH_RE.match(path) or not suffix_allowed(path):
                skipped.append(path)
                continue
            h = hashlib.sha256()
            size = 0
            with zf.open(_zinfo(f"{BUNDLE_ROBOTS_DIR}/{sv.robot_id}/{sv.version}/{path}"), mode="w") as fh:
                async for chunk in _logical_chunks(storage, sv.prefix + path):
                    h.update(chunk)
                    size += len(chunk)
                    fh.write(chunk)
                    out = sink.drain()
                    if out:
                        yield out
            files.append({"path": path, "sha256": h.hexdigest(), "bytes": size})
            out = sink.drain()
            if out:
                yield out
        meta = robot_metadata(sv.manifest or {})
        row = {
            "ref": sv.ref,
            "id": sv.robot_id,
            "version": sv.version,
            "version_scheme": version_scheme(sv.version),
            "latest_in_source": sv.version == latest,
            "name": meta.get("label") or sv.robot_id,
            "path": f"{BUNDLE_ROBOTS_DIR}/{sv.robot_id}/{sv.version}/",
            "manifest_sha256": next((f["sha256"] for f in files if f["path"] == MANIFEST_NAME), None),
            "content_sha256": content_sha256((f["path"], f["sha256"]) for f in files),
            "files": files,
            "metadata": meta,
        }
        if skipped:
            row["skipped_files"] = skipped
        robots.append(row)
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "format_version": BUNDLE_FORMAT_VERSION,
        "exported_at": exported_at,
        "adapy_version": _adapy_version(),
        "source": {"provider": PROVIDER_ID, "collection": COLLECTION},
        "version_order": "revision | semver | natural; see docs/architecture/robot_asset_bundles.md",
        "robots": robots,
    }
    zf.writestr(_zinfo(BUNDLE_MANIFEST), dump_json(manifest))
    zf.close()
    out = sink.drain()
    if out:
        yield out


# --- import: validation ---------------------------------------------------------------------------


@dataclass
class BundleRobot:
    ref: str
    robot_id: str
    version: str
    name: str
    files: dict[str, dict]  # path -> {"sha256", "bytes"} as the bundle's manifest says
    metadata: dict
    content_sha256: str
    manifest_sha256: Optional[str]
    problems: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.problems


@dataclass
class Bundle:
    manifest: dict
    robots: list[BundleRobot]

    def summary(self) -> dict:
        return {
            "schema": self.manifest.get("schema"),
            "exported_at": self.manifest.get("exported_at"),
            "adapy_version": self.manifest.get("adapy_version"),
            "robots": len(self.robots),
        }


def _unsafe_name(name: str) -> Optional[str]:
    if not name:
        return "an entry has no name"
    if "\\" in name:
        return f"{name!r}: backslashes are not allowed in a path"
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return f"{name!r}: absolute paths are not allowed"
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return f"{name!r}: empty, '.' or '..' segments are not allowed"
    if "\x00" in name:
        return f"{name!r}: NUL in a path"
    return None


def _is_symlink(zi: zipfile.ZipInfo) -> bool:
    return zi.create_system == 3 and stat.S_ISLNK(zi.external_attr >> 16)


def _source_size(source: BundleSource) -> int:
    if isinstance(source, (str, pathlib.Path)):
        return pathlib.Path(source).stat().st_size
    size = source.seek(0, io.SEEK_END)
    source.seek(0)
    return size


def validate_bundle(source: BundleSource, limits: Optional[Limits] = None) -> Bundle:
    """Everything about the bundle ``source`` (a path, or a seekable binary file such as the staged
    object read in place) that can be checked before a byte is written: it is a zip of this format,
    every entry is a safe relative path of an allowed type and within the limits, and every file is
    the size and sha256 the manifest says. Raises :class:`BundleError` for a bundle refused as a
    whole; a robot whose own files do not hold up keeps its ``problems`` and is not imported.

    Blocking (and, over a :class:`~ada.comms.rest.storage.RangeReader`, network) reads: run it in a
    worker thread."""
    limits = limits or Limits.from_env()
    size = _source_size(source)
    if size > limits.max_bundle_bytes:
        raise BundleError(f"the bundle is {size} bytes; the limit is {limits.max_bundle_bytes}")
    if not zipfile.is_zipfile(source):
        raise BundleError(f"not a {BUNDLE_SUFFIX} bundle: the file is not a zip")
    problems: list[str] = []
    with zipfile.ZipFile(source) as zf:
        infos = zf.infolist()
        if len(infos) > limits.max_files + 1:
            raise BundleError(f"the bundle has {len(infos)} entries; the limit is {limits.max_files}")
        names: dict[str, zipfile.ZipInfo] = {}
        total = 0
        for zi in infos:
            name = zi.filename
            if name.endswith("/"):
                continue  # a directory entry: nothing to write
            bad = _unsafe_name(name)
            if bad:
                problems.append(bad)
                continue
            if _is_symlink(zi):
                problems.append(f"{name!r}: symbolic links are not allowed")
                continue
            if zi.flag_bits & 0x1:
                problems.append(f"{name!r}: encrypted entries are not allowed")
                continue
            if name in names:
                problems.append(f"{name!r} is in the bundle twice")
                continue
            if zi.file_size > limits.max_file_bytes:
                problems.append(f"{name!r} is {zi.file_size} bytes; the limit is {limits.max_file_bytes}")
            if zi.file_size > (1 << 20) and zi.file_size > max(1, zi.compress_size) * limits.max_ratio:
                problems.append(f"{name!r} unpacks to {zi.file_size} bytes from {zi.compress_size}: refused")
            total += zi.file_size
            names[name] = zi
        if total > limits.max_total_bytes:
            problems.append(f"the bundle unpacks to {total} bytes; the limit is {limits.max_total_bytes}")
        if BUNDLE_MANIFEST not in names:
            problems.append(f"no {BUNDLE_MANIFEST} at the root: not a robot bundle")
        if problems:
            raise BundleError("the bundle is refused", problems)

        manifest = _read_bundle_manifest(zf, names[BUNDLE_MANIFEST], limits)
        robots_raw = manifest.get("robots")
        if not isinstance(robots_raw, list):
            raise BundleError(f"{BUNDLE_MANIFEST}: 'robots' must be a list")
        if len(robots_raw) > limits.max_robots:
            raise BundleError(f"the bundle has {len(robots_raw)} robots; the limit is {limits.max_robots}")

        robots: list[BundleRobot] = []
        claimed: set[str] = {BUNDLE_MANIFEST}
        seen_refs: set[str] = set()
        for i, raw in enumerate(robots_raw):
            robot = _robot_entry(raw, i, problems)
            if robot is None:
                continue
            if robot.ref in seen_refs:
                problems.append(f"{robot.ref} is in the manifest twice")
                continue
            seen_refs.add(robot.ref)
            base = f"{BUNDLE_ROBOTS_DIR}/{robot.robot_id}/{robot.version}/"
            for p in robot.files:
                full = base + p
                claimed.add(full)
                if not PATH_RE.match(p):
                    problems.append(f"{robot.ref}: {p!r} is not a valid file path")
                elif not suffix_allowed(p):
                    problems.append(f"{robot.ref}: {p!r} is not an allowed file type")
                elif full not in names:
                    robot.problems.append(f"{p} is named in the manifest and not in the bundle")
            if MANIFEST_NAME not in robot.files:
                robot.problems.append(f"no {MANIFEST_NAME}")
            robots.append(robot)
        for name in names:
            if name not in claimed:
                problems.append(f"{name!r} is in the bundle and not in its manifest")
        if problems:
            raise BundleError("the bundle is refused", problems)

        for robot in robots:
            _verify_robot(zf, names, robot, limits)
    return Bundle(manifest=manifest, robots=robots)


def _read_bundle_manifest(zf: zipfile.ZipFile, zi: zipfile.ZipInfo, limits: Limits) -> dict:
    if zi.file_size > limits.max_manifest_bytes:
        raise BundleError(f"{BUNDLE_MANIFEST} is {zi.file_size} bytes; the limit is {limits.max_manifest_bytes}")
    try:
        doc = json.loads(_read_capped(zf, zi, limits.max_manifest_bytes))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BundleError(f"{BUNDLE_MANIFEST} is not JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise BundleError(f"{BUNDLE_MANIFEST} must be a JSON object")
    schema = doc.get("schema")
    if not isinstance(schema, str) or not schema.startswith(BUNDLE_SCHEMA_FAMILY):
        raise BundleError(f"{BUNDLE_MANIFEST}: schema {schema!r} is not a robot bundle ({BUNDLE_SCHEMA})")
    if schema != BUNDLE_SCHEMA:
        raise BundleError(f"{BUNDLE_MANIFEST}: schema {schema!r} is newer than this server reads ({BUNDLE_SCHEMA})")
    return doc


def _robot_entry(raw: Any, i: int, problems: list[str]) -> Optional[BundleRobot]:
    where = f"robots[{i}]"
    if not isinstance(raw, dict):
        problems.append(f"{where} is not an object")
        return None
    robot_id, version = raw.get("id"), raw.get("version")
    if not isinstance(robot_id, str) or not ID_RE.match(robot_id):
        problems.append(f"{where}: id {robot_id!r} is not a robot id (lower-case letters, digits, - and _)")
        return None
    if not isinstance(version, str) or not VERSION_RE.match(version):
        problems.append(f"{where}: version {version!r} is not a version")
        return None
    files_raw = raw.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        problems.append(f"{where}: 'files' must be a non-empty list")
        return None
    files: dict[str, dict] = {}
    for f in files_raw:
        if (
            not isinstance(f, dict)
            or not isinstance(f.get("path"), str)
            or not isinstance(f.get("sha256"), str)
            or not SHA_RE.match(f["sha256"])
            or not isinstance(f.get("bytes"), int)
            or isinstance(f.get("bytes"), bool)
            or f["bytes"] < 0
        ):
            problems.append(f"{where}: every file needs a path, a sha256 (64 hex) and its bytes")
            return None
        if f["path"] in files:
            problems.append(f"{where}: {f['path']!r} is listed twice")
            return None
        files[f["path"]] = {"sha256": f["sha256"], "bytes": f["bytes"]}
    meta = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    name = raw.get("name") if isinstance(raw.get("name"), str) and raw.get("name") else robot_id
    content = content_sha256((p, f["sha256"]) for p, f in files.items())
    robot = BundleRobot(
        ref=asset_ref(robot_id, version),
        robot_id=robot_id,
        version=version,
        name=name,
        files=files,
        metadata=meta,
        content_sha256=content,
        manifest_sha256=files.get(MANIFEST_NAME, {}).get("sha256"),
    )
    claimed = raw.get("content_sha256")
    if claimed is not None and claimed != content:
        robot.problems.append("its content_sha256 is not the hash of its files")
    return robot


def _read_capped(zf: zipfile.ZipFile, zi: zipfile.ZipInfo, cap: int) -> bytes:
    with zf.open(zi) as fh:
        data = fh.read(cap + 1)
    if len(data) > cap:
        raise BundleError(f"{zi.filename!r} is larger than {cap} bytes")
    return data


def _verify_robot(zf: zipfile.ZipFile, names: dict[str, zipfile.ZipInfo], robot: BundleRobot, limits: Limits) -> None:
    """Each file's real bytes against its size and sha256 (read, never trusted from the header), and
    the robot.json against where it sits and the files it pins."""
    base = f"{BUNDLE_ROBOTS_DIR}/{robot.robot_id}/{robot.version}/"
    for p, want in sorted(robot.files.items()):
        zi = names.get(base + p)
        if zi is None:
            continue  # already a problem of the robot
        h = hashlib.sha256()
        n = 0
        try:
            with zf.open(zi) as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    n += len(chunk)
                    if n > limits.max_file_bytes:
                        raise BundleError(f"{zi.filename!r} unpacks past {limits.max_file_bytes} bytes")
                    h.update(chunk)
        except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
            robot.problems.append(f"{p} could not be read: {exc}")
            continue
        if n != want["bytes"]:
            robot.problems.append(f"{p} is {n} bytes; the manifest says {want['bytes']}")
        elif h.hexdigest() != want["sha256"]:
            robot.problems.append(f"{p} is not the file the manifest names (sha256 differs)")
    zi = names.get(base + MANIFEST_NAME)
    if zi is None or robot.problems:
        return
    try:
        doc = json.loads(_read_capped(zf, zi, limits.max_manifest_bytes))
    except (ValueError, UnicodeDecodeError, BundleError) as exc:
        robot.problems.append(f"{MANIFEST_NAME} is not JSON: {exc}")
        return
    if not isinstance(doc, dict):
        robot.problems.append(f"{MANIFEST_NAME} is not a JSON object")
        return
    if doc.get("id") != robot.robot_id or str(doc.get("version")) != robot.version:
        robot.problems.append(f"{MANIFEST_NAME} says it is {doc.get('id')}@{doc.get('version')}, not {robot.ref}")
    for p, sha in _pins(doc):
        f = robot.files.get(p)
        if f is None:
            robot.problems.append(f"{MANIFEST_NAME} names {p}, which is not in the bundle")
        elif f["sha256"] != sha:
            robot.problems.append(f"{MANIFEST_NAME} names {p} with another sha256 than the file's")
    if not robot.metadata:
        robot.metadata = robot_metadata(doc)


# --- import: what it would do ---------------------------------------------------------------------

#: What an import does with one robot of a bundle.
NEW = "new"  # no version of the id is stored: added, and it is the latest
UNCHANGED = "unchanged"  # the same version is stored with the same content: nothing to do
NEWER = "newer"  # newer than the latest stored version: added as a new version, the latest from then on
OLDER = "older"  # older than the latest and not stored: skipped (or added as a past version, on request)
CONFLICT = "conflict"  # the same version is stored with other content: never written
INVALID = "invalid"  # its own files do not hold up: never written


@dataclass
class Plan:
    robot: BundleRobot
    status: str
    latest_before: Optional[str] = None
    stored_versions: list[str] = field(default_factory=list)
    detail: str = ""
    duplicate_of: Optional[str] = None

    def as_dict(self) -> dict:
        r = self.robot
        return {
            "ref": r.ref,
            "id": r.robot_id,
            "version": r.version,
            "version_scheme": version_scheme(r.version),
            "name": r.name,
            "status": self.status,
            "detail": self.detail,
            "latest_before": self.latest_before,
            "stored_versions": self.stored_versions,
            "files": len(r.files),
            "bytes": sum(f["bytes"] for f in r.files.values()),
            "content_sha256": r.content_sha256,
            "metadata": r.metadata,
            "problems": r.problems,
            "duplicate_of": self.duplicate_of,
        }


async def plan_import(storage, bundle: Bundle, store: dict[str, dict[str, StoredVersion]]) -> list[Plan]:
    """What importing ``bundle`` into ``store`` would do, robot by robot. Matching is by the asset id
    (the folder name and ``robot.json``'s ``id``); each is compared with the versions stored before
    the import -- two versions of one id in a bundle are each held against the store, not each other."""
    by_manifest = {
        sv.manifest_sha256: sv.ref
        for versions in store.values()
        for sv in versions.values()
        if sv.complete and sv.manifest_sha256
    }
    plans: list[Plan] = []
    for robot in bundle.robots:
        versions = complete_versions(store, robot.robot_id)
        latest = latest_version(versions)
        stored = sorted(versions, key=version_key)
        plan = Plan(robot=robot, status=NEW, latest_before=latest, stored_versions=stored)
        if not robot.valid:
            plan.status, plan.detail = INVALID, "; ".join(robot.problems)
        elif robot.version in versions:
            content, _ = await stored_content(storage, versions[robot.version])
            if content == robot.content_sha256:
                plan.status, plan.detail = UNCHANGED, "this version is stored with the same content"
            else:
                plan.status = CONFLICT
                plan.detail = (
                    f"{robot.ref} is stored with other content; a stored version is never overwritten -- "
                    "give the changed asset a new version"
                )
        elif latest is None:
            plan.status, plan.detail = NEW, f"adds {robot.robot_id} at version {robot.version}"
        elif version_key(robot.version) > version_key(latest):
            plan.status, plan.detail = NEWER, f"adds version {robot.version} (the latest was {latest})"
        else:
            plan.status = OLDER
            plan.detail = f"older than the latest stored version {latest}: skipped unless added as a past version"
        dup = by_manifest.get(robot.manifest_sha256) if robot.manifest_sha256 else None
        if dup and dup != robot.ref:
            plan.duplicate_of = dup
        plans.append(plan)
    return plans


def status_counts(plans: Iterable[Plan]) -> dict[str, int]:
    out = {s: 0 for s in (NEW, NEWER, UNCHANGED, OLDER, CONFLICT, INVALID)}
    for p in plans:
        out[p.status] = out.get(p.status, 0) + 1
    return out


# --- import: writing ------------------------------------------------------------------------------

#: What became of a robot of the bundle.
ADDED = "added"
ADDED_OLDER = "added-older"
SKIPPED = "skipped"
FAILED = "failed"


@dataclass(frozen=True)
class Choices:
    """What the admin chose in the preview: past versions to add (``add_older`` for all of them, or
    per ref), and refs to leave out."""

    add_older: bool = False
    add_older_refs: frozenset[str] = frozenset()
    keep_older_refs: frozenset[str] = frozenset()  # an explicit "no" for one ref beats the bundle-wide yes
    exclude: frozenset[str] = frozenset()

    @classmethod
    def from_body(cls, body: dict) -> "Choices":
        """``{add_older?: bool, robots?: {ref: {include?: bool, add_older?: bool}}}``."""
        add_older = body.get("add_older", False)
        if not isinstance(add_older, bool):
            raise ValueError("'add_older' must be true or false")
        robots = body.get("robots") or {}
        if not isinstance(robots, dict):
            raise ValueError("'robots' must be an object keyed by ref")
        add, keep, excl = set(), set(), set()
        for ref, choice in robots.items():
            if not isinstance(choice, dict):
                raise ValueError(f"robots[{ref!r}] must be an object")
            for name in ("include", "add_older"):
                if name in choice and not isinstance(choice[name], bool):
                    raise ValueError(f"robots[{ref!r}].{name} must be true or false")
            if choice.get("include") is False:
                excl.add(ref)
            if choice.get("add_older") is True:
                add.add(ref)
            elif choice.get("add_older") is False:
                keep.add(ref)
        return cls(add_older, frozenset(add), frozenset(keep), frozenset(excl))

    def adds_older(self, ref: str) -> bool:
        if ref in self.keep_older_refs:
            return False
        return self.add_older or ref in self.add_older_refs


async def _put_member(storage, zf: zipfile.ZipFile, name: str, key: str) -> None:
    """One file of the bundle into its object, straight from the zip: no local copy. A file up to
    :data:`_PART` is one PUT; a larger one streams up as a multipart upload, a chunk at a time.
    The zip reads (range requests, inflating) are blocking and run in a worker thread. zipfile
    checks the member's CRC as its last bytes are read, so a corrupt member raises before the
    upload completes and nothing is stored under ``key``."""
    src = await asyncio.to_thread(zf.open, name)
    try:
        head = await asyncio.to_thread(src.read, _PART + 1)
        if len(head) <= _PART:
            await storage.put_bytes(STORE, key, head)
            return

        async def chunks() -> AsyncIterator[bytes]:
            yield head
            while True:
                chunk = await asyncio.to_thread(src.read, _CHUNK)
                if not chunk:
                    break
                yield chunk

        await storage.put_stream(STORE, key, chunks(), chunk_size=_PART)
    finally:
        src.close()


async def _write_robot(storage, zf: zipfile.ZipFile, robot: BundleRobot, existing: Optional[StoredVersion]) -> None:
    """Write one version: every file but ``robot.json`` first, ``robot.json`` last -- a version
    without it is incomplete and nothing uses it -- so a reader sees all of it or none. Each file
    streams from the zip into its object (:func:`_put_member`). Leftovers of an incomplete earlier
    attempt at the same version are removed. On any failure what was written is deleted again."""
    prefix = asset_prefix(robot.robot_id, robot.version)
    base = f"{BUNDLE_ROBOTS_DIR}/{robot.robot_id}/{robot.version}/"
    written: list[str] = []
    order = sorted(p for p in robot.files if p != MANIFEST_NAME) + [MANIFEST_NAME]
    try:
        for p in order:
            await _put_member(storage, zf, base + p, prefix + p)
            written.append(prefix + p)
        if existing is not None:
            stale = [prefix + p for p in existing.files if p not in robot.files]
            if stale:
                await storage.delete_many(STORE, stale)
    except BaseException:
        # robot.json is last, so a failure leaves at most an incomplete version; remove even that.
        for key in reversed(written):
            try:
                await storage.delete(STORE, key)
            except Exception:  # noqa: BLE001 - best effort; an incomplete version is not used anyway
                pass
        raise


async def apply_import(
    storage,
    source: BundleSource,
    bundle: Bundle,
    plans: list[Plan],
    choices: Choices,
    store: dict[str, dict[str, StoredVersion]],
) -> list[dict]:
    """Carry out ``plans`` from the bundle ``source`` (a path, or the staged object read in place):
    each robot on its own (one failing does not stop the rest), in id and version order. Returns one
    result row per robot of the bundle."""
    results: list[dict] = []
    if not isinstance(source, (str, pathlib.Path)):
        source.seek(0)
    zf = await asyncio.to_thread(zipfile.ZipFile, source)  # reads the central directory
    with zf:
        for plan in sorted(plans, key=lambda p: (p.robot.robot_id, version_key(p.robot.version))):
            r = plan.robot
            row = {"ref": r.ref, "id": r.robot_id, "version": r.version, "name": r.name, "planned": plan.status}
            if r.ref in choices.exclude:
                results.append({**row, "result": SKIPPED, "detail": "left out in the preview"})
                continue
            if plan.status in (UNCHANGED, CONFLICT, INVALID):
                results.append(
                    {**row, "result": SKIPPED if plan.status != CONFLICT else CONFLICT, "detail": plan.detail}
                )
                continue
            if plan.status == OLDER and not choices.adds_older(r.ref):
                results.append({**row, "result": SKIPPED, "detail": plan.detail})
                continue
            existing = store.get(r.robot_id, {}).get(r.version)  # an incomplete leftover, if any
            try:
                await _write_robot(storage, zf, r, existing)
            except Exception as exc:  # noqa: BLE001 - reported per robot; the others go on
                results.append({**row, "result": FAILED, "detail": f"not imported: {type(exc).__name__}: {exc}"})
                continue
            result = ADDED_OLDER if plan.status == OLDER else ADDED
            detail = (
                f"added as a past version; the latest stays {plan.latest_before}"
                if result == ADDED_OLDER
                else plan.detail
            )
            results.append({**row, "result": result, "detail": detail})
    return results


# --- the index ------------------------------------------------------------------------------------


async def write_index(storage) -> dict:
    """Rewrite ``assets/robots/index.json`` from what is stored: one row per complete version. The
    index's ``schema`` stays what it was where there was one."""
    store = await list_store(storage)
    schema = INDEX_SCHEMA
    try:
        old = json.loads(await storage.get_bytes(STORE, INDEX_KEY))
        if isinstance(old, dict) and isinstance(old.get("schema"), str):
            schema = old["schema"]
    except Exception:  # noqa: BLE001 - no index yet, or an unreadable one: write a new one
        pass
    rows = []
    for robot_id in sorted(store):
        for version in sorted(complete_versions(store, robot_id), key=version_key):
            sv = store[robot_id][version]
            doc = sv.manifest or {}
            row: dict = {
                "ref": sv.ref,
                "id": robot_id,
                "version": version,
                "label": doc.get("label"),
                "manifest_sha256": sv.manifest_sha256,
                "levels": robot_metadata(doc).get("levels", []),
                "files": dict(sorted(sv.files.items())),
            }
            for name in ("roles", "payload_kg", "reach_m"):
                if name in doc:
                    row[name] = doc[name]
            rows.append(row)
    doc = {"schema": schema, "assets": rows}
    await storage.put_bytes(STORE, INDEX_KEY, dump_json(doc))
    return doc


# --- staging --------------------------------------------------------------------------------------


def staging_key(upload_id: str) -> str:
    if not UPLOAD_ID_RE.match(upload_id or ""):
        raise ValueError("not an upload id")
    return f"{STAGING_PREFIX}/{upload_id}{BUNDLE_SUFFIX}"


async def open_staged(storage, key: str) -> IO[bytes]:
    """The staged bundle at ``key``, opened for reading in place: the store's own file on a
    filesystem store, else range reads over the object with a few 4 MiB blocks cached. Nothing is
    copied to local disk. The caller closes it; read it from a worker thread."""
    return await storage.open_seekable(STORE, key)


async def sweep_staging(storage, now: Optional[_dt.datetime] = None) -> int:
    """Remove staged bundles older than :data:`STAGING_TTL_S`. Returns how many."""
    now = now or _dt.datetime.now(tz=_dt.timezone.utc)
    old = []
    for entry in await storage.list_prefix(STORE, STAGING_PREFIX + "/"):
        try:
            lm = _dt.datetime.fromisoformat(str(entry.last_modified).replace("Z", "+00:00"))
        except ValueError:
            continue
        if lm.tzinfo is None:
            lm = lm.replace(tzinfo=_dt.timezone.utc)
        if (now - lm).total_seconds() > STAGING_TTL_S:
            old.append(entry.key)
    if old:
        await storage.delete_many(STORE, old)
    return len(old)
