"""Reading ``GET /api/workers/bootstrap``: what a remote worker joins with, from the host URL alone."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

__all__ = ["BootstrapError", "WorkerBootstrap", "fetch_bootstrap", "parse_bootstrap"]

BOOTSTRAP_PATH = "/api/workers/bootstrap"
BOOTSTRAP_SCHEMA = "ada.viewer/worker-bootstrap@1"


class BootstrapError(RuntimeError):
    """The host did not answer with a bootstrap document."""


@dataclass(frozen=True)
class WorkerBootstrap:
    api_url: str
    nats_url: str | None
    grants_enabled: bool
    subject_prefix: str
    ttl_s: int
    part_size: int
    max_size: int
    small_object_max: int
    min_ada_py: str | None
    api_version: str | None
    #: The deployment's own block per capability, as served (never interpreted here either).
    capabilities: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    def capability(self, cap: str) -> dict:
        block = self.capabilities.get(cap)
        return dict(block) if isinstance(block, dict) else {}

    def inbox_prefix(self, worker_name: str) -> str:
        """The private inbox prefix this worker connects with (``_INBOX_<worker name>``)."""
        return f"_INBOX_{worker_name}"

    def grant_subject(self, capability: str, worker_name: str) -> str:
        return f"{self.subject_prefix}.{capability}.{worker_name}"


def parse_bootstrap(api_url: str, doc: object) -> WorkerBootstrap:
    if not isinstance(doc, dict) or doc.get("schema") != BOOTSTRAP_SCHEMA:
        raise BootstrapError(f"{api_url}{BOOTSTRAP_PATH} is not a worker bootstrap document (an older API?)")
    grants = doc.get("grants") or {}
    multipart = grants.get("multipart") or {}
    return WorkerBootstrap(
        api_url=api_url.rstrip("/"),
        nats_url=(doc.get("nats") or {}).get("url") or None,
        grants_enabled=bool(grants.get("enabled")),
        subject_prefix=str(grants.get("subject_prefix") or "ada.viewer.grants"),
        ttl_s=int(grants.get("ttl_s") or 900),
        part_size=int(multipart.get("part_size") or 64 * 1024 * 1024),
        max_size=int(multipart.get("max_size") or 0),
        small_object_max=int(grants.get("small_object_max") or 8 * 1024 * 1024),
        min_ada_py=(doc.get("worker") or {}).get("min_ada_py") or None,
        api_version=doc.get("api_version") or None,
        capabilities=dict(doc.get("capabilities") or {}),
        raw=doc,
    )


def fetch_bootstrap(api_url: str, *, timeout_s: float = 15.0, opener=None) -> WorkerBootstrap:
    """GET the host's bootstrap. ``api_url`` is the host URL (``https://studio.example``)."""
    base = api_url.strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise BootstrapError(f"not an http(s) URL: {api_url!r}")
    req = urllib.request.Request(base + BOOTSTRAP_PATH, headers={"Accept": "application/json"})
    open_ = opener or urllib.request.urlopen
    try:
        with open_(req, timeout=timeout_s) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        hint = " (the API predates remote-worker bootstrap)" if exc.code == 404 else ""
        raise BootstrapError(f"{base}{BOOTSTRAP_PATH} answered {exc.code}{hint}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BootstrapError(f"{base}{BOOTSTRAP_PATH} is unreachable: {type(exc).__name__}: {exc}") from exc
    try:
        doc = json.loads(body)
    except ValueError as exc:
        raise BootstrapError(f"{base}{BOOTSTRAP_PATH} did not answer JSON") from exc
    return parse_bootstrap(base, doc)
