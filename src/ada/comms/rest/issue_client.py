"""Thin async client for GitHub / Forgejo issue APIs.

Both forges expose nearly identical ``/repos/{owner}/{repo}/issues``
endpoints; we abstract the differences (auth header shape, base URL,
search-by-label payload) behind a small :class:`GitForgeClient`
protocol so the issue-bot dispatch loop in
:mod:`ada.comms.rest.app` doesn't need a branch per kind.

Endpoints we exercise:

* ``GET    /repos/{repo}/issues?labels=...&state=...`` — find by label.
* ``POST   /repos/{repo}/issues`` — open a new issue with body + labels.
* ``POST   /repos/{repo}/issues/{number}/comments`` — comment on existing.
* ``PATCH  /repos/{repo}/issues/{number}`` — update the body (used to
  rebuild the dashboard issue without spawning duplicates).

The client is intentionally minimal — we don't wrap the full forge
APIs, just the verbs the bot uses. Errors propagate as
:class:`IssueClientError` with the HTTP status + body so the caller
can log a single line and mark the run ``issue_bot_status='failed'``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

import httpx

from ada.config import logger


class IssueClientError(RuntimeError):
    """Raised on non-2xx responses or transport errors. Carries the
    HTTP status + body so the caller can surface a useful diagnostic
    in the audit-runs UI."""

    def __init__(self, message: str, *, status: int | None = None, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class IssueRef:
    """One issue as the client sees it. ``number`` is the integer used
    in URLs; ``html_url`` is the user-facing link surfaced in the
    dashboard. Both forges return both fields with the same key
    name, so the same dataclass works for either."""

    number: int
    title: str
    body: str | None
    html_url: str | None
    labels: list[str]
    state: str


class GitForgeClient(Protocol):
    """Subset of the issue API used by :mod:`audit_issue`'s bot
    loop. Both :class:`GitHubClient` and :class:`ForgejoClient`
    satisfy this protocol so the dispatch loop stays forge-agnostic."""

    async def list_issues_by_label(
        self, label: str, *, state: str = "open",
    ) -> list[IssueRef]: ...

    async def find_issue_by_title(self, title: str) -> IssueRef | None: ...

    async def create_issue(
        self, *, title: str, body: str, labels: Iterable[str],
    ) -> IssueRef: ...

    async def comment_issue(self, number: int, *, body: str) -> None: ...

    async def update_issue_body(self, number: int, *, body: str) -> None: ...


# ── Base implementation (shared by GitHub + Forgejo) ───────────────


class _BaseClient:
    """Common request plumbing. Subclasses provide the auth header
    shape + the base URL."""

    def __init__(self, *, base_url: str, repo: str, token: str):
        if "/" not in repo:
            raise ValueError(
                f"repo {repo!r} must be 'owner/name'",
            )
        self._base = base_url.rstrip("/")
        self._repo = repo
        self._token = token
        # 15 s is generous enough that a slow forge doesn't trip us,
        # tight enough that a stalled call doesn't pin the poller.
        self._timeout = httpx.Timeout(15.0)

    def _headers(self) -> dict[str, str]:
        raise NotImplementedError

    def _issues_url(self, *suffix: str) -> str:
        parts = (self._base, "repos", self._repo, "issues", *suffix)
        return "/".join(p.strip("/") for p in parts)

    async def _request(
        self, method: str, url: str, **kwargs,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                r = await client.request(
                    method, url, headers=self._headers(), **kwargs,
                )
            except httpx.HTTPError as exc:
                raise IssueClientError(
                    f"{method} {url} transport failure: {exc}",
                ) from exc
        if r.status_code >= 400:
            raise IssueClientError(
                f"{method} {url} → {r.status_code}",
                status=r.status_code,
                body=r.text[:500],
            )
        return r

    async def list_issues_by_label(
        self, label: str, *, state: str = "open",
    ) -> list[IssueRef]:
        # Both forges return a JSON array; pagination is per_page=100
        # by default which suffices — the audit panel won't have
        # thousands of open fingerprints (if it does, we have bigger
        # problems than pagination).
        r = await self._request(
            "GET", self._issues_url(),
            params={"labels": label, "state": state, "per_page": 100},
        )
        return [_parse_issue(d) for d in r.json()]

    async def find_issue_by_title(self, title: str) -> IssueRef | None:
        # No dedicated search call — the dashboard is a single row so
        # filter the open issues list and compare titles. State='all'
        # so a manually-closed dashboard still gets re-found and
        # reopened by the body-update path.
        r = await self._request(
            "GET", self._issues_url(),
            params={"state": "all", "per_page": 100},
        )
        for d in r.json():
            if d.get("title") == title:
                return _parse_issue(d)
        return None

    async def create_issue(
        self, *, title: str, body: str, labels: Iterable[str],
    ) -> IssueRef:
        payload = {
            "title": title,
            "body": body,
            "labels": list(labels),
        }
        r = await self._request("POST", self._issues_url(), json=payload)
        return _parse_issue(r.json())

    async def comment_issue(self, number: int, *, body: str) -> None:
        await self._request(
            "POST",
            self._issues_url(str(number), "comments"),
            json={"body": body},
        )

    async def update_issue_body(self, number: int, *, body: str) -> None:
        await self._request(
            "PATCH", self._issues_url(str(number)), json={"body": body},
        )


def _parse_issue(d: dict) -> IssueRef:
    return IssueRef(
        number=int(d.get("number", 0)),
        title=d.get("title") or "",
        body=d.get("body"),
        html_url=d.get("html_url"),
        labels=[
            (lab.get("name") if isinstance(lab, dict) else str(lab))
            for lab in (d.get("labels") or [])
        ],
        state=d.get("state") or "",
    )


# ── GitHub ─────────────────────────────────────────────────────────


class GitHubClient(_BaseClient):
    """github.com / GHE issues API.

    base_url defaults to the public API; pass an enterprise URL
    (``https://github.example.com/api/v3``) for self-hosted.
    """

    def __init__(
        self, *, repo: str, token: str,
        base_url: str = "https://api.github.com",
    ):
        super().__init__(base_url=base_url, repo=repo, token=token)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }


# ── Forgejo ────────────────────────────────────────────────────────


class ForgejoClient(_BaseClient):
    """Forgejo (Gitea-compatible) issues API.

    base_url is the full host root including ``/api/v1``, e.g.
    ``https://git.example.com/api/v1``.
    """

    def __init__(self, *, repo: str, token: str, base_url: str):
        super().__init__(base_url=base_url, repo=repo, token=token)

    def _headers(self) -> dict[str, str]:
        # Forgejo / Gitea accepts both ``token <T>`` and ``Bearer <T>``;
        # the legacy ``token`` prefix works against older Gitea forks
        # that some deployments still run.
        return {
            "Authorization": f"token {self._token}",
            "Accept": "application/json",
        }


# ── Factory ────────────────────────────────────────────────────────


def build_client(
    kind: str, *, repo: str, token: str, base_url: str | None = None,
) -> GitForgeClient:
    """Construct the right client for the configured forge kind.

    ``kind`` is the wire string stored in ``app_settings``
    (``audit.issue_target.kind``). For unknown kinds we raise
    :class:`ValueError` — the poller catches it and marks the run
    ``issue_bot_status='failed'`` with the message so the admin
    sees a useful diagnostic in the UI.
    """
    k = kind.strip().lower()
    if k == "github":
        return GitHubClient(
            repo=repo, token=token,
            base_url=base_url or "https://api.github.com",
        )
    if k == "forgejo" or k == "gitea":
        if not base_url:
            raise ValueError(
                "forgejo client requires base_url "
                "(e.g. https://git.example.com/api/v1)",
            )
        return ForgejoClient(repo=repo, token=token, base_url=base_url)
    raise ValueError(f"unknown issue target kind {kind!r}")


# Re-exported so callers don't have to fish into this module to type-
# hint their interfaces.
__all__ = [
    "GitForgeClient",
    "GitHubClient",
    "ForgejoClient",
    "IssueRef",
    "IssueClientError",
    "build_client",
]


# Touch the logger so an unused-import lint pass keeps the import.
# The logger is intentionally available for callers that want to
# wrap this module's calls in their own try/except.
_ = logger
