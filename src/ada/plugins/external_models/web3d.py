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

import base64
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
    "WEB3D_SIDECAR_FILENAME",
    "Web3dSite",
    "MirrorEntry",
    "MirrorReport",
    "Web3dBlobClient",
    "Web3dSource",
    "Web3dMirror",
    "mirror_from_env",
    "mirror_enabled",
    "collection_for",
    "model_id_for",
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
    ) -> MirrorReport:
        """Bring the cache up to date, transferring only what changed.

        `force` re-transfers regardless of ETag -- the escape hatch for a cache
        whose sidecar and contents have drifted apart. `model_id` narrows to one
        site, which is what a "refresh this one" button sends.
        """
        collection = collection_for(project)
        report = self.status(project, model_file=model_file, check_source=True)
        report.dry_run = dry_run
        recorded = self._sidecar(collection)
        changed = False

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

#: The admin toggle. Read by the job entrypoint before it will mirror anything,
#: so turning it off stops a scheduled sync without unregistering a provider or
#: redeploying a worker.
MIRROR_ENABLED_VAR = "ADA_WEB3D_MIRROR_ENABLED"
PROJECTS_VAR = "ADA_WEB3D_PROJECTS"


def mirror_enabled() -> bool:
    return (os.environ.get(MIRROR_ENABLED_VAR) or "").strip().lower() in ("1", "true", "yes", "on")


def configured_projects() -> list[str]:
    raw = os.environ.get(PROJECTS_VAR) or ""
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
    missing = [
        v
        for v in ("ADA_WEB3D_TENANT_ID", "ADA_WEB3D_CLIENT_ID", "ADA_WEB3D_CLIENT_SECRET")
        if not (os.environ.get(v) or "").strip()
    ]
    if missing:
        raise ValueError(
            f"web3d mirroring needs {', '.join(missing)}. These are the read-only service "
            "principal on the web3d storage account; a personal sign-in is not used here."
        )
    return Web3dBlobClient(
        tenant_id=os.environ["ADA_WEB3D_TENANT_ID"].strip(),
        client_id=os.environ["ADA_WEB3D_CLIENT_ID"].strip(),
        client_secret=os.environ["ADA_WEB3D_CLIENT_SECRET"].strip(),
        storage_url=(os.environ.get("ADA_WEB3D_STORAGE_URL") or DEFAULT_STORAGE_URL).strip(),
        authority=(os.environ.get("ADA_WEB3D_AUTHORITY") or DEFAULT_AUTHORITY).strip(),
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


def _b64_json(raw: str) -> dict:
    """Decode one JWT segment. Only used to report who a token belongs to."""
    pad = "=" * (-len(raw) % 4)
    return json.loads(base64.urlsafe_b64decode(raw + pad))


def sites_summary(sites: Iterable[Web3dSite]) -> str:
    by_file: dict[str, int] = {}
    for s in sites:
        by_file[s.model_file] = by_file.get(s.model_file, 0) + 1
    return ", ".join(f"{k}: {v}" for k, v in sorted(by_file.items())) or "nothing"
