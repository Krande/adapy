"""Mirror web3d's published GLBs into the external-models cache, so the viewer
can serve them to anyone signed in.

WHY A MIRROR AND NOT A LIVE PROVIDER. web3d publishes one GLB per E3D SITE,
rebuilt nightly, in Azure blob storage. Reaching them through web3d's own access
API needs a caller whose identity carries an Aibel Repro allocation -- it derives
project access from an `employeeid` claim -- so a service principal is served the
public `000000` registry and refused every project container:

    list_models("ASP") -> Failed to get a container SAS for 'asp':
    400 {"success":false,"message":"User missing employee id"}

That is a decision on web3d's side, not a missing grant, and it is why every
consumer so far has had to sign a REAL PERSON in interactively before it could
read anything. A read-only service principal on the STORAGE ACCOUNT goes around
it: the blobs are the same blobs, and `https://storage.azure.com/.default` is a
credential the access API is not in the path of.

So this reads web3d once, with that credential, and writes what it finds into
the deployment's own external-models store. From then on the viewer serves the
GLBs the way it serves every other external model -- a presigned GET from its
own bucket -- and a consumer needs nothing but a signed-in adapy session or a
CLI token. Nobody signs into web3d again.

WHAT MAKES IT A CACHE RATHER THAN A COPY. Every mirrored object is recorded in a
per-collection sidecar with the SOURCE blob's ETag. A sync compares that against
a live HEAD and transfers only what actually changed, so a nightly run over a
project whose sites did not rebuild costs one HEAD per site and no bytes. The
same comparison, without the transfer, is `status()` -- which is what an admin
panel shows and what a cron job exits non-zero on.

THE TRAVERSAL is web3d's own, ported from `web3dDirect.ts` by way of
`asa-weld-gen`'s puller, and it is three hops of JSON before any geometry:

    000000/project_list.json          every project, with its container
    <container>/<config blob>         `model_data`: the source exports
    <container>/<model file path>     `models`: the sites, each with a glb_url

The config blob's name is NOT derivable from the container -- `gua` holds
`project_config_gua.json` while `asp-weld` holds `project_config_asp.json` -- so
it is read from the project row rather than constructed.

NOTHING HERE WRITES TO WEB3D. Every call against the storage account is a GET or
a HEAD, and the credential is expected to be read-only. The only thing this
module writes is the external-models cache.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

__all__ = [
    "MIRROR_ENABLED_VAR",
    "PROJECTS_VAR",
    "TENANT_VAR",
    "CLIENT_ID_VAR",
    "CLIENT_SECRET_VAR",
    "WEB3D_SIDECAR_FILENAME",
    "Web3dSite",
    "MirrorEntry",
    "MirrorReport",
    "Web3dBlobClient",
    "Web3dSource",
    "Web3dMirror",
    "mirror_from_env",
    "mirror_enabled",
    "mirror_configured",
    "configured_projects",
    "read_mirror_setting",
    "MIRROR_SETTING_KEY",
    "collection_for",
    "model_id_for",
    "Web3dMirrorCatalog",
    "provider_from_env",
    "can_hold_a_mirror",
    "is_a_mirror",
]

#: Per-collection record of what was mirrored and from where. A sidecar rather
#: than S3 object metadata for the same reason `_labels.json` is one: a listing
#: does not carry user metadata, so per-object state would cost one HEAD per
#: model on every page load. One GET answers for the whole collection.
#:
#: Named with a leading underscore so it sorts beside `_labels.json` and is
#: excluded from model listings by the same suffix check.
WEB3D_SIDECAR_FILENAME = "_web3d.json"

#: The Azure REST API version the requests declare. Pinned rather than latest:
#: a version bump changes response shapes, and nothing here benefits from one.
BLOB_API_VERSION = "2021-08-06"

#: web3d's public catalogue container. Readable by anyone with any credential on
#: the account, which is what makes project discovery work before any project
#: has been named.
PROJECT_CONTAINER = "000000"
PROJECT_LIST_BLOB = "project_list.json"

DEFAULT_STORAGE_URL = "https://web3dstandard.blob.core.windows.net"
DEFAULT_AUTHORITY = "https://login.microsoftonline.com"
STORAGE_SCOPE = "https://storage.azure.com/.default"

HTTP_TIMEOUT = 300


# =============================================================================
# What a mirrored thing is
# =============================================================================


@dataclass(frozen=True)
class Web3dSite:
    """One loadable GLB in web3d, and everything needed to fetch it again."""

    project: str
    model_file: str
    site: str
    container: str
    path: str

    @property
    def model_id(self) -> str:
        return model_id_for(self.model_file, self.site)


@dataclass(frozen=True)
class MirrorEntry:
    """One site's cache state: what is stored, what web3d has, and whether they
    are the same thing.

    `source_etag` is None when the comparison was not made -- a status read that
    was told not to touch web3d, or a HEAD that failed. That is deliberately a
    THIRD state next to fresh and stale: "we did not look" must not render as
    "up to date", which is the one answer a staleness display cannot afford to
    guess.
    """

    collection: str
    model_id: str
    site: Web3dSite
    cached_etag: str | None = None
    source_etag: str | None = None
    mirrored_at: str | None = None
    size: int | None = None

    @property
    def cached(self) -> bool:
        return self.cached_etag is not None

    @property
    def stale(self) -> bool | None:
        if self.source_etag is None:
            return None
        return self.cached_etag != self.source_etag

    def as_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["site"] = dataclasses.asdict(self.site)
        d["cached"] = self.cached
        d["stale"] = self.stale
        return d


@dataclass
class MirrorReport:
    """What one status or sync call found. Counts first, because that is what an
    admin panel and a cron exit code are actually made of."""

    entries: list[MirrorEntry] = field(default_factory=list)
    transferred: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False

    @property
    def cached_count(self) -> int:
        return sum(1 for e in self.entries if e.cached)

    @property
    def stale_count(self) -> int:
        return sum(1 for e in self.entries if e.stale)

    @property
    def unknown_count(self) -> int:
        """Entries whose freshness was not established. See `MirrorEntry.stale`."""
        return sum(1 for e in self.entries if e.stale is None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": len(self.entries),
            "cached": self.cached_count,
            "stale": self.stale_count,
            "unknown": self.unknown_count,
            "transferred": list(self.transferred),
            "failed": dict(self.failed),
            "dry_run": self.dry_run,
            "entries": [e.as_dict() for e in self.entries],
        }


def collection_for(project: str) -> str:
    """The cache collection one web3d project lands in.

    Lower-cased and stripped of separators because a collection is ONE path
    segment in the store -- `S3ExternalModelCatalog._upload_key` refuses
    anything else, and a project key with a slash in it would otherwise write
    outside the collection it named.
    """
    key = (project or "").strip().strip("/").lower()
    if not key:
        raise ValueError("a web3d project key is required")
    return key.replace("/", "-").replace("\\", "-")


def model_id_for(model_file: str, site: str) -> str:
    """`<model file>~<site>.glb` -- the same name the CLI puller writes.

    THE TILDE IS LOad-BEARING. A site is only unique within its model file
    (`ModelExportMain.rvm` and `ModelExportWeld.rvm` both publish
    `/AP400-STRU_MS`), so a name built from the site alone silently mirrors one
    over the other. The separator has to be something neither half contains, and
    a slash would make it a second level the catalogue does not have.
    """
    stem = f"{_flatten(model_file)}~{_flatten(site)}"
    return f"{stem}.glb"


def _flatten(part: str) -> str:
    out = (part or "").strip().strip("/")
    for bad in ("/", "\\"):
        out = out.replace(bad, "_")
    return out or "unnamed"


# =============================================================================
# The source: Azure blob storage, read with a service principal
# =============================================================================


class Web3dBlobClient:
    """GET and HEAD against one storage account, authorised as a service
    principal.

    Hand-rolled on urllib, and the reasoning is the same as the CLI puller's: two
    form posts and a couple of GETs do not justify `azure-identity`, which drags
    in `msal` and `cryptography` behind it. adapy is a library that many
    environments install -- a dependency added here is paid for by all of them.

    The token is cached and re-minted on expiry. Client-credentials tokens have
    no refresh token: re-minting IS the refresh.
    """

    #: Re-mint this long before expiry rather than racing the clock on a slow
    #: download of a 40 MB GLB.
    REFRESH_MARGIN = 5 * 60

    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        storage_url: str = DEFAULT_STORAGE_URL,
        authority: str = DEFAULT_AUTHORITY,
    ) -> None:
        if not (tenant_id and client_id and client_secret):
            raise ValueError("a web3d blob client needs tenant_id, client_id and client_secret")
        self._tenant = tenant_id
        self._client_id = client_id
        self._secret = client_secret
        self.storage_url = storage_url.rstrip("/")
        self._authority = authority.rstrip("/")
        self._token: str | None = None
        self._expires_at: float = 0.0

    # --- auth ----------------------------------------------------------------

    def access_token(self) -> str:
        import time

        if self._token and self._expires_at - self.REFRESH_MARGIN > time.time():
            return self._token

        url = f"{self._authority}/{self._tenant}/oauth2/v2.0/token"
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._secret,
                "scope": STORAGE_SCOPE,
            }
        ).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            detail = e.read()[:500].decode("utf-8", "replace")
            # The secret is never in this message; the response body from AAD
            # names the failure (expired secret, wrong tenant, missing role) and
            # is the only thing that distinguishes them.
            raise RuntimeError(f"web3d: could not mint a storage token ({e.code}): {detail}") from None

        token = payload.get("access_token")
        if not token:
            raise RuntimeError("web3d: the token response carried no access_token")
        self._token = str(token)
        self._expires_at = time.time() + float(payload.get("expires_in") or 3600)
        return self._token

    # --- blobs ---------------------------------------------------------------

    def _request(self, method: str, container: str, path: str) -> urllib.request.Request:
        url = f"{self.storage_url}/{container}/{_encode_blob_path(path)}"
        return urllib.request.Request(
            url,
            method=method,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "x-ms-version": BLOB_API_VERSION,
                # Asking for the stored bytes rather than a decoded stream. A
                # web3d GLB is stored gzipped with `Content-Encoding: gzip`, and
                # urllib does NOT inflate for us -- so what arrives is the
                # compressed body and the header is how we know.
                "Accept-Encoding": "gzip",
            },
        )

    def head(self, container: str, path: str) -> dict[str, str] | None:
        """Headers of one blob, or None when it is not there."""
        try:
            with urllib.request.urlopen(self._request("HEAD", container, path), timeout=HTTP_TIMEOUT) as r:
                return {k.lower(): v for k, v in r.headers.items()}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise

    def etag(self, container: str, path: str) -> str:
        headers = self.head(container, path) or {}
        return (headers.get("etag") or "").strip('"')

    def get(self, container: str, path: str) -> tuple[bytes, bool, str]:
        """`(body, is_gzipped, etag)` -- the body exactly as stored."""
        with urllib.request.urlopen(self._request("GET", container, path), timeout=HTTP_TIMEOUT) as r:
            raw = r.read()
            enc = (r.headers.get("Content-Encoding") or "").lower()
            etag = (r.headers.get("ETag") or "").strip('"')
        return raw, enc == "gzip", etag

    def get_json(self, container: str, path: str) -> Any:
        raw, gzipped, _ = self.get(container, path)
        if gzipped:
            raw = gzip.decompress(raw)
        return json.loads(raw)

    def list_containers(self) -> list[str]:
        """Account-level listing. Used only by `check_credential`, because the
        traversal below never needs to guess a container name."""
        url = f"{self.storage_url}/?comp=list"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "x-ms-version": BLOB_API_VERSION,
            },
        )
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            root = ET.fromstring(r.read())
        return [n.text or "" for n in root.iter("Name")]


def _encode_blob_path(path: str) -> str:
    """Percent-encode a blob path, keeping the separators.

    Blob names routinely carry spaces and parentheses -- `/AP400(511)-STRU` is a
    real E3D site name -- and an unencoded request for one is a 400 with no
    mention of the character that caused it.
    """
    return "/".join(urllib.parse.quote(seg, safe="") for seg in str(path).lstrip("/").split("/"))


class Web3dSource:
    """web3d's catalogue, read straight from the blobs.

    The three hops in the module docstring, and nothing else. It does not know
    about caching, S3, or the viewer.
    """

    def __init__(self, client: Web3dBlobClient) -> None:
        self._client = client
        self._config_cache: dict[str, Any] = {}

    def list_projects(self) -> list[dict[str, str]]:
        rows = self._client.get_json(PROJECT_CONTAINER, PROJECT_LIST_BLOB)
        out: list[dict[str, str]] = []
        for row in rows or []:
            # Inactive projects are hidden from web3d's own picker; mirroring one
            # would publish something its owners have withdrawn.
            if row.get("active") is False:
                continue
            out.append(
                {
                    "key": row["unique_key"],
                    "container": row["container"],
                    "name": row.get("plant_name") or row["unique_key"],
                    "config": row.get("config") or "",
                }
            )
        return out

    def project(self, key: str) -> dict[str, str]:
        wanted = (key or "").strip().lower()
        for p in self.list_projects():
            if p["key"].lower() == wanted:
                return p
        listed = ", ".join(sorted(p["key"] for p in self.list_projects())) or "nothing"
        raise KeyError(f"web3d lists no active project {key!r}; it lists {listed}")

    def list_sites(self, project_key: str, model_file: str | None = None) -> list[Web3dSite]:
        project = self.project(project_key)
        if not project["config"]:
            raise RuntimeError(
                f"web3d project {project['key']!r} names no config blob, so its model files "
                "cannot be read"
            )
        config = self._client.get_json(project["container"], project["config"])

        out: list[Web3dSite] = []
        seen: dict[str, Any] = {}
        for entry in config.get("model_data") or []:
            model_id = entry["name"]
            if model_file is not None and model_id != model_file:
                continue
            # A model export may live in a container other than its project's.
            # Ignoring `container_override` reads the wrong container in silence.
            container = entry.get("container_override") or project["container"]
            cache_key = f"{container}/{entry['path']}"
            sites = seen.get(cache_key)
            if sites is None:
                sites = self._client.get_json(container, entry["path"])
                seen[cache_key] = sites
            for site_id, details in (sites.get("models") or {}).items():
                glb = details.get("glb_url") or ""
                if not glb:
                    continue
                out.append(
                    Web3dSite(
                        project=project["key"],
                        model_file=model_id,
                        site=details.get("name") or site_id,
                        container=container,
                        path=glb,
                    )
                )
        return out


# =============================================================================
# The cache seam
# =============================================================================


@runtime_checkable
class MirrorCache(Protocol):
    """What the mirror needs from a store, and no more.

    `S3ExternalModelCatalog` satisfies this; so does a fake in a test. Stated as
    a Protocol rather than taken as a concrete class so the mirror never reaches
    for a bucket, a client or a credential of its own -- the store it was handed
    owns all three.
    """

    def list_models(self, collection: str) -> list: ...

    def model_upload_url(
        self, collection: str, model_id: str, *, expires_in_seconds: int = 900, content_type: str | None = None
    ) -> str: ...

    #: Optional, and preferred when present: a write from THIS process, with the
    #: content headers carried alongside the bytes.
    def put_model(self, collection: str, model_id: str, body: bytes, headers: dict | None = None) -> None: ...

    def model_upload_headers(self, collection: str, model_id: str) -> dict[str, str]: ...

    def get_sidecar(self, collection: str, filename: str) -> dict: ...

    def put_sidecar(self, collection: str, filename: str, data: dict) -> None: ...


# =============================================================================
# The mirror
# =============================================================================


class Web3dMirror:
    """Keep a cache collection equal to what web3d publishes for one project.

    Two entry points, and the first is half of the second: `status` establishes
    what would be transferred, `sync` transfers it. They are separate because
    the question "is the cache current?" is asked far more often than the answer
    is acted on -- by an admin panel that renders it and by a cron job that
    exits on it -- and answering it costs one HEAD per site while acting on it
    costs the bytes.
    """

    def __init__(self, source: Web3dSource, cache: MirrorCache, *, client: Web3dBlobClient) -> None:
        self._source = source
        self._cache = cache
        self._client = client

    # --- state ----------------------------------------------------------------

    def _sidecar(self, collection: str) -> dict[str, dict[str, Any]]:
        raw = self._cache.get_sidecar(collection, WEB3D_SIDECAR_FILENAME) or {}
        # Tolerated rather than validated: a hand-edited or half-written sidecar
        # should degrade to "nothing is known to be cached", which re-mirrors,
        # rather than failing the panel that reads it.
        return {str(k): v for k, v in raw.items() if isinstance(v, dict)}

    def status(self, project: str, *, model_file: str | None = None, check_source: bool = True) -> MirrorReport:
        """What is cached for one project, and what web3d has moved on from.

        `check_source=False` skips the per-site HEAD. The result then reports
        every entry's staleness as unknown -- see `MirrorEntry.stale` -- which is
        what a panel wants on first paint, before it has been asked to go and
        look.
        """
        collection = collection_for(project)
        recorded = self._sidecar(collection)
        # What is ACTUALLY in the store, which is not necessarily what the
        # sidecar claims: an object deleted out from under it must read as
        # missing, not as cached.
        present = {m.id for m in self._cache.list_models(collection)}

        report = MirrorReport()
        for site in self._source.list_sites(project, model_file):
            model_id = site.model_id
            # `list_models` ids are filename-derived and carry no suffix.
            stem = model_id[: -len(".glb")] if model_id.endswith(".glb") else model_id
            note = recorded.get(model_id) or {}
            cached_etag = str(note.get("etag") or "") or None
            if stem not in present:
                cached_etag = None

            source_etag: str | None = None
            if check_source:
                try:
                    source_etag = self._client.etag(site.container, site.path) or None
                except Exception as e:  # noqa: BLE001 - one unreachable blob must not sink the report
                    logger.warning("web3d: HEAD failed for %s/%s: %s", site.container, site.path, e)
                    report.failed[model_id] = str(e)

            report.entries.append(
                MirrorEntry(
                    collection=collection,
                    model_id=model_id,
                    site=site,
                    cached_etag=cached_etag,
                    source_etag=source_etag,
                    mirrored_at=str(note.get("mirrored_at") or "") or None,
                    size=note.get("size") if isinstance(note.get("size"), int) else None,
                )
            )
        return report

    # --- transfer -------------------------------------------------------------

    def sync(
        self,
        project: str,
        *,
        model_file: str | None = None,
        model_id: str | None = None,
        force: bool = False,
        dry_run: bool = False,
        on_progress=None,
    ) -> MirrorReport:
        """Bring the cache up to date, transferring only what changed.

        `force` re-transfers regardless of ETag -- the escape hatch for a cache
        whose sidecar and contents have drifted apart. `model_id` narrows to one
        site, which is what a "refresh this one" button sends.

        `on_progress(done, total, model_id)` is called once per site actually
        transferred. PER SITE, not per project: a first mirror of ASP is 208
        sites and tens of minutes, and a progress bar that moves once at the end
        of all of them is a progress bar that never moves. It is also the only
        place that knows the denominator -- `status` has already decided which
        sites are stale by the time the transfers begin.
        """
        collection = collection_for(project)
        report = self.status(project, model_file=model_file, check_source=True)
        report.dry_run = dry_run
        recorded = self._sidecar(collection)
        changed = False

        # The denominator the caller sees. Counted before the loop so a progress
        # fraction is over the work actually queued, not over every site in the
        # project -- on a second run those differ by two orders of magnitude.
        todo = [
            e
            for e in report.entries
            if (not model_id or e.model_id == model_id)
            and e.model_id not in report.failed
            and (force or e.stale is not False)
        ]
        total = len(todo)
        done = 0

        for entry in report.entries:
            if model_id and entry.model_id != model_id:
                continue
            if entry.model_id in report.failed:
                continue
            if not force and entry.stale is False:
                continue
            if dry_run:
                report.transferred.append(entry.model_id)
                continue
            try:
                size = self._transfer(collection, entry)
            except Exception as e:  # noqa: BLE001 - one failed site must not stop the rest
                logger.warning("web3d: mirroring %s failed: %s", entry.model_id, e)
                report.failed[entry.model_id] = str(e)
                continue
            recorded[entry.model_id] = {
                "etag": entry.source_etag,
                "container": entry.site.container,
                "path": entry.site.path,
                "project": entry.site.project,
                "model_file": entry.site.model_file,
                "site": entry.site.site,
                "size": size,
                "mirrored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            report.transferred.append(entry.model_id)
            changed = True
            done += 1
            if on_progress is not None:
                try:
                    on_progress(done, total, entry.model_id)
                except Exception:  # noqa: BLE001 - progress must never sink a transfer
                    logger.debug("web3d: progress callback failed", exc_info=True)

        # Written once at the end rather than per object: a sidecar rewritten per
        # transfer is N round-trips and N chances to leave it describing half a
        # sync. A crash mid-way loses the record of what was transferred, which
        # the next run re-transfers -- the safe direction.
        if changed:
            self._cache.put_sidecar(collection, WEB3D_SIDECAR_FILENAME, recorded)
        return report

    def _transfer(self, collection: str, entry: MirrorEntry) -> int:
        """One site, web3d -> cache. Returns the number of bytes stored.

        THE BYTES ARE NOT RE-ENCODED WHERE THEY ARRIVE ALREADY GZIPPED. The cache
        asks for gzip with `Content-Encoding: gzip` (see
        `S3ExternalModelCatalog.model_upload_headers`) and web3d stores its GLBs
        gzipped, so the common path is a byte-for-byte pass-through. A blob that
        arrives uncompressed is compressed here, because storing raw bytes under
        a header claiming gzip reaches the viewer as a JSON parse error naming
        neither compression nor the file.
        """
        raw, gzipped, _ = self._client.get(entry.site.container, entry.site.path)
        headers = dict(self._cache.model_upload_headers(collection, entry.model_id) or {})
        wants_gzip = (headers.get("Content-Encoding") or "").lower() == "gzip"

        if wants_gzip and not gzipped:
            raw = gzip.compress(raw)
        elif gzipped and not wants_gzip:
            raw = gzip.decompress(raw)

        # DIRECT WHERE THE STORE ALLOWS IT. `model_upload_url` signs against the
        # PUBLIC endpoint -- correct for the browser it was written for, and
        # inside a container `localhost:3900` is the container, so the PUT comes
        # back `Connection refused`. That is not a misconfiguration to work
        # around: a worker holding the credential should not be asking itself
        # for a signature to use against itself.
        direct = getattr(self._cache, "put_model", None)
        if callable(direct):
            # The headers go WITH the bytes. A direct write is the one path that
            # does not run through an uploader obeying `model_upload_headers`,
            # so dropping them here stores gzip with nothing saying so -- which
            # the viewer reports as `Unexpected token '\x1f' ... is not valid
            # JSON`, naming neither compression nor the file.
            direct(collection, entry.model_id, raw, headers)
            return len(raw)

        url = self._cache.model_upload_url(
            collection, entry.model_id, content_type=headers.get("Content-Type")
        )
        req = urllib.request.Request(url, data=raw, method="PUT", headers=headers)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT):
            pass
        return len(raw)


# =============================================================================
# Configuration
# =============================================================================

#: The admin toggle. Read before anything is mirrored, so turning it off stops a
#: scheduled sync without unregistering a provider or redeploying a worker. It
#: is a `public.`-prefixed viewer setting in the deployment and an environment
#: variable here; either turning it off is enough.
MIRROR_ENABLED_VAR = "ASA_WEB3D_MIRROR_ENABLED"
PROJECTS_VAR = "ASA_WEB3D_PROJECTS"

#: The read-only service principal on the web3d STORAGE ACCOUNT. Named for
#: where they come from -- Vault, on asa-viewer -- rather than renamed on the
#: way in, so an operator grepping for a secret finds the same string in the
#: vault, the deployment and this file.
#:
#: `_RO_` is load-bearing and worth keeping in the name: nothing in this module
#: writes to web3d, the principal is not granted write, and a future reader
#: should be able to see that from the variable alone.
TENANT_VAR = "ASA_WEB3D_RO_ST_TENANT_ID"
CLIENT_ID_VAR = "ASA_WEB3D_RO_ST_CLIENT_ID"
CLIENT_SECRET_VAR = "ASA_WEB3D_RO_ST_CLIENT_SECRET"


#: The admin toggle, as a viewer setting rather than an environment variable.
#:
#: WHY IT IS NOT ENFORCED IN THE WORKER. Settings live in the API's database and
#: the worker that runs a plugin job has no pool -- so the process doing the
#: mirroring cannot read this, and pretending otherwise would be a switch that
#: silently does nothing. Both CALLERS can read it and do: the admin panel, from
#: the browser, and :func:`main` below, through the API with a CLI token. The
#: environment variable stays as the deployment-level master switch underneath
#: it, which is the half an operator with shell access can always reach.
#:
#: `public.` is load-bearing: any authenticated user may READ a key in that
#: namespace and only an admin may write one, which is exactly the access this
#: needs -- every user's panel has to know whether the cache is live, and only
#: an admin may change it.
MIRROR_SETTING_KEY = "public.external_models.web3d_mirror"

API_BASE_VAR = "ADAPY_API_BASE"
API_TOKEN_VAR = "ADAPY_API_TOKEN"


def read_mirror_setting() -> dict[str, Any] | None:
    """The admin toggle, read through the viewer API, or None.

    None is a THIRD answer and not a default: it means the setting could not be
    consulted -- no API configured, or it did not answer -- which is different
    from an admin having switched mirroring off. A caller falls back to the
    environment on None and refuses on `{"enabled": false}`.
    """
    base = (os.environ.get(API_BASE_VAR) or "").strip().rstrip("/")
    token = (os.environ.get(API_TOKEN_VAR) or "").strip()
    if not base or not token:
        return None
    url = f"{base}/api/settings/{urllib.parse.quote(MIRROR_SETTING_KEY, safe='')}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read() or b"{}")
    except Exception as e:  # noqa: BLE001 - an unreachable API is "unknown", not "off"
        logger.warning("web3d: could not read %s: %s", MIRROR_SETTING_KEY, e)
        return None

    raw = payload.get("value")
    if raw in (None, ""):
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            logger.warning("web3d: %s is not valid JSON", MIRROR_SETTING_KEY)
            return None
    return raw if isinstance(raw, dict) else None


def mirror_enabled() -> bool:
    """Is mirroring switched on?

    The setting wins where it exists, because it is the one an admin can change
    without a redeploy. The environment variable answers when it does not.
    """
    setting = read_mirror_setting()
    if setting is not None and "enabled" in setting:
        return bool(setting["enabled"])
    return (os.environ.get(MIRROR_ENABLED_VAR) or "").strip().lower() in ("1", "true", "yes", "on")


def mirror_configured() -> bool:
    """Whether the credential is present at all.

    Separate from :func:`mirror_enabled` because they are different answers to
    an admin panel: "your deployment has no web3d credential" and "you switched
    this off" call for different things to do next, and a single boolean makes
    the first look like the second.
    """
    return all((os.environ.get(v) or "").strip() for v in (TENANT_VAR, CLIENT_ID_VAR, CLIENT_SECRET_VAR))


def configured_projects() -> list[str]:
    """Which web3d projects this deployment mirrors, setting first.

    Same precedence as the switch, and for the same reason: adding a project to
    the cache should not need a redeploy.
    """
    setting = read_mirror_setting() or {}
    raw = setting.get("projects")
    if isinstance(raw, list):
        out = [str(x).strip() for x in raw if str(x).strip()]
        if out:
            return list(dict.fromkeys(out))
    if isinstance(raw, str) and raw.strip():
        return _split_projects(raw)
    return _split_projects(os.environ.get(PROJECTS_VAR) or "")


def _split_projects(raw: str) -> list[str]:
    out: list[str] = []
    for part in raw.replace(";", ",").split(","):
        key = part.strip()
        if key and key not in out:
            out.append(key)
    return out


def blob_client_from_env() -> Web3dBlobClient:
    """The read-only service principal, from the environment.

    Every one of these is a deployment fact, and the secret is the only one that
    is sensitive -- so the error names which variable is missing and never what
    any of them contained.
    """
    missing = [v for v in (TENANT_VAR, CLIENT_ID_VAR, CLIENT_SECRET_VAR) if not (os.environ.get(v) or "").strip()]
    if missing:
        raise ValueError(
            f"web3d mirroring needs {', '.join(missing)}. These are the read-only service "
            "principal on the web3d storage account, injected from Vault; a personal sign-in "
            "is deliberately not used here, because a cache nobody can refresh unattended is "
            "not a cache."
        )
    return Web3dBlobClient(
        tenant_id=os.environ[TENANT_VAR].strip(),
        client_id=os.environ[CLIENT_ID_VAR].strip(),
        client_secret=os.environ[CLIENT_SECRET_VAR].strip(),
        storage_url=(os.environ.get("ASA_WEB3D_STORAGE_URL") or DEFAULT_STORAGE_URL).strip(),
        authority=(os.environ.get("ASA_WEB3D_AUTHORITY") or DEFAULT_AUTHORITY).strip(),
    )


def mirror_from_env(cache: MirrorCache | None = None) -> Web3dMirror:
    """Build a mirror from the environment and the deployment's own catalogue.

    `cache` defaults to the configured external-models catalogue, which is the
    whole point: the mirror writes where the viewer already reads, so a mirrored
    model needs no second provider and no second binding.
    """
    if cache is None:
        from ada.plugins.external_models.catalog import demo_catalog_from_env

        cache = demo_catalog_from_env()  # type: ignore[assignment]
    client = blob_client_from_env()
    return Web3dMirror(Web3dSource(client), cache, client=client)  # type: ignore[arg-type]


def sites_summary(sites: Iterable[Web3dSite]) -> str:
    by_file: dict[str, int] = {}
    for s in sites:
        by_file[s.model_file] = by_file.get(s.model_file, 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(by_file.items())) or "nothing"


# =============================================================================
# The provider: web3d as an external-model catalogue, served from the cache
# =============================================================================


class Web3dMirrorCatalog:
    """web3d as an external-model provider, backed by the mirror.

    WHY THIS EXISTS RATHER THAN A DIRECT READER. The browser-side `web3d`
    provider reads as the SIGNED-IN USER, and refuses when there is no session:

        web3d is read as the signed-in user, and this viewer has no signed-in
        session

    -- which is every deployment running with auth off, and every scheduled job.
    The service principal removes the need for a session, but it must not be
    handed to a browser: a bearer for the storage account is a credential for
    EVERY project on it, and `model_download_headers` would put it in page
    JavaScript.

    So the bytes come the long way round and the credential never leaves the
    worker: LIST from web3d, SERVE from this deployment's own store, and MIRROR
    on first request. A presigned GET from the viewer's own bucket is a URL that
    grants one object for fifteen minutes, which is what a browser should be
    given.

    THE LISTING IS LIVE, NOT THE CACHE'S. A collection lists what web3d
    publishes, whether or not it has been mirrored yet -- otherwise a fresh
    deployment shows an empty catalogue and there is no way to ask for anything.
    `ExternalModel.labelled` is reused to say which entries are already local;
    `mirror_status` is the detailed answer.
    """

    def __init__(self, source: "Web3dSource", cache, mirror: "Web3dMirror", projects: list[str]):
        self._source = source
        self._cache = cache
        self._mirror = mirror
        self._projects = projects

    # --- the three required methods -----------------------------------------

    def list_collections(self) -> list:
        from ada.plugins.external_models.catalog import Collection

        keys = self._projects or [p["key"] for p in self._source.list_projects()]
        return [Collection(id=collection_for(k), name=k) for k in sorted(set(keys))]

    def list_models(self, collection: str) -> list:
        from ada.plugins.external_models.catalog import ExternalModel

        project = self._project_for(collection)
        cached = {m.id for m in self._cache.list_models(collection)}
        out = []
        for site in self._source.list_sites(project):
            model_id = site.model_id
            stem = model_id[: -len(".glb")] if model_id.endswith(".glb") else model_id
            out.append(
                ExternalModel(
                    id=stem,
                    # THE SITE ALONE. It used to be "<model file> / <site>",
                    # which reads well in web3d's own UI and badly in a narrow
                    # list: `ModelExportMain.rvm / ` is the same 20 characters
                    # on nearly every row, and it is the SITE that is being
                    # looked for. The model file moves to the description,
                    # where a hover finds it.
                    #
                    # Two model files can publish the same site, so these names
                    # can collide on screen. The `id` does not -- it carries
                    # both -- and the description is what tells them apart.
                    name=site.site,
                    description=f"{site.model_file} · {site.project}",
                    collection=collection,
                    key=f"{collection}/{model_id}",
                    # Reused to mean "already in this deployment's store". A UI
                    # that renders it as "curated" is not wrong either -- both
                    # say the entry is more than a filename.
                    labelled=stem in cached,
                )
            )
        return sorted(out, key=lambda m: m.name)

    def model_download_url(self, collection: str, model_id: str, *, expires_in_seconds: int = 900) -> str:
        """A presigned GET from the cache, mirroring the model first if it is
        not there yet.

        ON-DEMAND, and the first open of a 40 MB site therefore takes as long as
        the transfer. The alternative -- refusing until a scheduled sync has run
        -- makes a correctly configured deployment look broken for a day.
        """
        project = self._project_for(collection)
        stem = model_id[: -len(".glb")] if model_id.endswith(".glb") else model_id
        stored = f"{stem}.glb"

        if stem not in {m.id for m in self._cache.list_models(collection)}:
            report = self._mirror.sync(project, model_id=stored)
            if stored in report.failed:
                raise RuntimeError(f"web3d: could not mirror {stored}: {report.failed[stored]}")
            if stored not in report.transferred:
                raise KeyError(f"web3d publishes no model {model_id!r} in {collection!r}")

        return self._cache.model_download_url(collection, stem, expires_in_seconds=expires_in_seconds)

    # --- the mirror's own surface, so the admin panel can reach it -----------

    def upstream_projects(self) -> list[dict]:
        """Every project the service principal can see, and which are mirrored.

        THE CONFIGURED LIST IS NOT THIS LIST. `list_collections` answers with
        what this deployment MIRRORS, which is the handful an admin chose;
        this answers with what it COULD, which on the ASP account is 95. A panel
        offering the choice needs the second, and deriving it from the first is
        impossible -- the whole point is to see the ones you have not picked.

        `selected` rides along so the caller needs one round trip rather than
        two, and cannot render a checkbox list against a stale selection.
        """
        chosen = {collection_for(k) for k in self._projects}
        out = []
        for p in self._source.list_projects():
            out.append(
                {
                    "key": p["key"],
                    "name": p.get("name") or p["key"],
                    "collection": collection_for(p["key"]),
                    "selected": collection_for(p["key"]) in chosen,
                }
            )
        return sorted(out, key=lambda x: x["key"])

    def mirror_status(self, project: str, **kw):
        return self._mirror.status(project, **kw)

    def mirror_sync(self, project: str, **kw):
        return self._mirror.sync(project, **kw)

    #: `on_progress` rides through `**kw` above. Named here because it is the
    #: one keyword a caller has to know about and the signature no longer shows
    #: it: see `Web3dMirror.sync`.

    def _project_for(self, collection: str) -> str:
        wanted = (collection or "").strip().lower()
        for key in self._projects or [p["key"] for p in self._source.list_projects()]:
            if collection_for(key) == wanted:
                return key
        # Not a failure to look up: a collection id IS a lower-cased project
        # key, so the round trip is exact for every project that exists.
        return collection


def can_hold_a_mirror(cache) -> bool:
    """Can this store accept a mirror? Somewhere to write, and somewhere to
    record what was written.

    Shared with the job layer so the panel's `can_mirror` and the code that
    actually refuses cannot drift apart.
    """
    writable = any(callable(getattr(cache, a, None)) for a in ("put_model", "model_upload_url"))
    return writable and all(
        callable(getattr(cache, attr, None))
        for attr in ("model_upload_headers", "get_sidecar", "put_sidecar")
    )


def is_a_mirror(catalogue) -> bool:
    """Does this PROVIDER carry a mirror of its own?

    `can_hold_a_mirror` asks whether a store can be filled; this asks whether a
    catalogue already owns the thing that fills it. `Web3dMirrorCatalog` does --
    it holds the source, the cache and the mirror -- and it is not itself a
    store, so the first question answers False for the one provider that most
    obviously has a mirror. Both are needed, and conflating them reported
    `can_mirror=False` on the web3d provider.
    """
    return all(callable(getattr(catalogue, attr, None)) for attr in ("mirror_status", "mirror_sync"))


def provider_from_env():
    """The `web3d` provider, or a refusal explaining which half is missing.

    Registered as a FACTORY, so nothing here runs until someone actually asks
    for the provider -- which is what keeps a deployment with no credential from
    paying for one at import time.
    """
    if not mirror_configured():
        raise ValueError(
            f"web3d needs the read-only service principal on the storage account "
            f"({TENANT_VAR}, {CLIENT_ID_VAR}, {CLIENT_SECRET_VAR}). Without it web3d can only be "
            "read as a signed-in user, which is what the browser-side provider does."
        )

    from ada.plugins.external_models.catalog import demo_catalog_from_env

    cache = demo_catalog_from_env()
    if not can_hold_a_mirror(cache):
        raise ValueError(
            "web3d serves its models from THIS deployment's own store, and the configured "
            "external-model catalogue cannot hold them -- it has no upload or sidecar surface. "
            "Set ADA_EXTERNAL_MODELS_CATALOG=s3 and its bucket: the credential stays in the "
            "worker and the browser is given a presigned GET, which is the whole point."
        )

    client = blob_client_from_env()
    source = Web3dSource(client)
    return Web3dMirrorCatalog(source, cache, Web3dMirror(source, cache, client=client), configured_projects())


# =============================================================================
# The scheduled half
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    """`python -m ada.plugins.external_models.web3d check|sync` -- the cron entry.

    IT RUNS THE SAME TWO CALLS THE ADMIN PANEL DOES, deliberately: `check` is
    `status`, `sync` is `sync`. A scheduled job that took a different path from
    the button would drift from it, and the drift would only ever show up as a
    cache that is stale in a way the panel says it is not.

    EXIT CODES ARE THE POINT OF `check`. A cron wrapper needs to act on the
    answer without parsing anything:

        0  every cached model matches web3d
        1  something is stale, or is not cached at all
        2  the question could not be asked -- no credential, mirroring off,
           or web3d unreachable

    `sync` returns 0 when it transferred everything it meant to and 1 when any
    site failed, so a job that half-succeeds is not reported as a success. Note
    that `sync` deliberately does NOT treat "nothing needed transferring" as a
    failure: a nightly run over an unchanged project is the normal case and the
    expensive one to get wrong.
    """
    import argparse

    ap = argparse.ArgumentParser(prog="web3d-mirror", description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=("check", "sync"))
    ap.add_argument("projects", nargs="*", help=f"web3d project keys; default is {PROJECTS_VAR}")
    ap.add_argument("--model-file", default=None, help="only this model file's sites")
    ap.add_argument("--force", action="store_true", help="re-transfer even where the ETag matches")
    ap.add_argument("--dry-run", action="store_true", help="say what would be transferred, write nothing")
    ap.add_argument(
        "--ignore-disabled",
        action="store_true",
        help=f"sync even when {MIRROR_ENABLED_VAR} is off",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not mirror_configured():
        logger.error(
            "no web3d credential: %s / %s / %s must all be set", TENANT_VAR, CLIENT_ID_VAR, CLIENT_SECRET_VAR
        )
        return 2
    if args.command == "sync" and not mirror_enabled() and not args.ignore_disabled:
        logger.error("web3d mirroring is switched off (%s); --ignore-disabled runs it anyway", MIRROR_ENABLED_VAR)
        return 2

    projects = args.projects or configured_projects()
    if not projects:
        logger.error("no project named and %s is not set", PROJECTS_VAR)
        return 2

    try:
        from ada.plugins.external_models.catalog import demo_catalog_from_env

        cache = demo_catalog_from_env()
        if not can_hold_a_mirror(cache):
            # The stub is the DEFAULT catalogue, so this is the first thing a
            # freshly configured deployment hits. Saying which setting is
            # missing beats an AttributeError from inside `status`.
            logger.error(
                "the configured external-model catalogue cannot hold a mirror: it has no upload "
                "or sidecar surface. Set ADA_EXTERNAL_MODELS_CATALOG=s3 and its bucket."
            )
            return 2
        mirror = mirror_from_env(cache)
    except Exception as e:  # noqa: BLE001 - a missing bucket or credential is a 2, not a crash
        logger.error("cannot build the mirror: %s", e)
        return 2

    worst = 0
    for project in projects:
        try:
            if args.command == "check":
                report = mirror.status(project, model_file=args.model_file, check_source=True)
            else:
                report = mirror.sync(
                    project, model_file=args.model_file, force=args.force, dry_run=args.dry_run
                )
        except Exception as e:  # noqa: BLE001 - one unreachable project must not hide the others
            logger.error("%s: %s", project, e)
            worst = max(worst, 2)
            continue

        logger.info(
            "%s: %d site(s), %d cached, %d stale, %d unchecked%s",
            project,
            len(report.entries),
            report.cached_count,
            report.stale_count,
            report.unknown_count,
            f", {len(report.transferred)} transferred" if args.command == "sync" else "",
        )
        for model_id, why in sorted(report.failed.items()):
            logger.warning("  %s: %s", model_id, why)

        if report.failed:
            worst = max(worst, 1)
        if args.command == "check" and (report.stale_count or report.cached_count < len(report.entries)):
            worst = max(worst, 1)

    return worst


if __name__ == "__main__":  # pragma: no cover - the cron entry
    raise SystemExit(main())
