"""More than one CI bot per project.

One bot per project forced every consumer to share one credential, and the CLI
revoke cutoff is stored per SUBJECT — so rotating for one consumer silently
broke the others, and every audit row read ``ci:<slug>`` no matter which of
them acted. A name gives each consumer its own subject, which by itself gives
each one its own cutoff. Nothing in the token or revocation model changes.

Stubbed pool and auth rather than live Postgres: what this route decides —
which subject it builds, what names it refuses, and whether revoking mints —
is all above the database.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-ci-bot-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

PROJECT = "6f1e9c9a-0000-4000-8000-000000000001"
SLUG = "asp"
MINT = f"/api/admin/projects/{PROJECT}/ci-bot"
REVOKE = f"{MINT}/revoke"


def _settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=None,
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="ada-viewer-jobs",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(
            enabled=False,
            issuer="",
            client_id="",
            audience="",
            admin_group="",
            cli_token_secret="secret",
        ),
        database_url="",
    )


class _FakePool:
    """Answers the one query the route makes: the project's slug."""

    def __init__(self, slug: str | None = SLUG):
        self.slug = slug

    async def fetchrow(self, *args, **kwargs):
        return None if self.slug is None else {"slug": self.slug}


@pytest.fixture
def ci_client(tmp_path: pathlib.Path, monkeypatch):
    """Yields ``(client, calls)``; ``calls`` records every side effect."""
    from ada.comms.rest import auth as auth_mod
    from ada.comms.rest import db as db_mod

    calls: dict = {"users": [], "members": [], "revoked": [], "minted": []}

    async def fake_upsert_user(pool, sub, email, display):
        calls["users"].append({"sub": sub, "email": email, "display": display})

    async def fake_add_member(pool, pid, sub, role="member"):
        calls["members"].append({"sub": sub, "role": role})

    async def fake_revoke(pool, user):
        calls["revoked"].append(user.sub)
        return 1700000000

    def fake_mint(user, config):
        calls["minted"].append(user.sub)
        return f"token-for-{user.sub}", 1700000000

    monkeypatch.setattr(db_mod, "upsert_user", fake_upsert_user)
    monkeypatch.setattr(db_mod, "add_project_member", fake_add_member)
    monkeypatch.setattr(auth_mod, "revoke_cli_tokens", fake_revoke)
    monkeypatch.setattr(auth_mod, "mint_cli_token", fake_mint)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.db_pool = _FakePool()
        yield client, calls


def test_no_name_keeps_the_original_subject(ci_client):
    """Backward compatibility is the load-bearing case: an existing bot must
    keep its subject, or every token already issued under it is orphaned by
    this feature shipping."""
    client, calls = ci_client
    r = client.post(MINT, json={})
    assert r.status_code == 201
    assert r.json()["user_sub"] == f"ci:{SLUG}"
    assert calls["minted"] == [f"ci:{SLUG}"]


def test_a_name_adds_one_segment(ci_client):
    client, calls = ci_client
    r = client.post(MINT, json={"name": "e3d-worker"})
    assert r.status_code == 201
    assert r.json()["user_sub"] == f"ci:{SLUG}:e3d-worker"
    # Still non-admin, still a project member, still role `ci`.
    assert calls["members"] == [{"sub": f"ci:{SLUG}:e3d-worker", "role": "ci"}]


def test_two_named_bots_are_separate_principals(ci_client):
    """The whole point. Rotating one must revoke only that one — which follows
    from them being different subjects, since the cutoff is per subject."""
    client, calls = ci_client
    client.post(MINT, json={"name": "ada-build"})
    client.post(MINT, json={"name": "e3d-worker"})
    assert calls["revoked"] == [f"ci:{SLUG}:ada-build", f"ci:{SLUG}:e3d-worker"]


def test_a_colon_in_a_name_is_refused(ci_client):
    """The colon is the separator. A name containing one could spell another
    bot's subject and quietly inherit its tokens and its revocation."""
    client, calls = ci_client
    r = client.post(MINT, json={"name": "a:b"})
    assert r.status_code == 400
    assert "colon" in r.json()["detail"]
    assert calls["minted"] == []


@pytest.mark.parametrize("name", ["-leading", "has space", "sla/sh", "x" * 65, "üñî"])
def test_malformed_names_are_refused(ci_client, name):
    client, calls = ci_client
    assert client.post(MINT, json={"name": name}).status_code == 400
    assert calls["minted"] == []


def test_a_name_is_lowercased_rather_than_refused(ci_client):
    """A subject is an identifier: accepting both `E3D` and `e3d` would make
    two bots that look like one wherever it is displayed."""
    client, _ = ci_client
    r = client.post(MINT, json={"name": "E3D-Worker"})
    assert r.json()["user_sub"] == f"ci:{SLUG}:e3d-worker"


def test_revoking_does_not_mint(ci_client):
    """For a leaked credential or a retired consumer: rotating would hand back
    a fresh secret nobody asked for and leave the bot able to act."""
    client, calls = ci_client
    r = client.post(REVOKE, json={"name": "ada-build"})
    assert r.status_code == 200
    assert r.json()["user_sub"] == f"ci:{SLUG}:ada-build"
    assert calls["revoked"] == [f"ci:{SLUG}:ada-build"]
    assert calls["minted"] == []
    # Membership is left alone: removing it is a separate deliberate act, and
    # keeping it means this bot's audit history still names a principal.
    assert calls["members"] == []


def test_an_unknown_project_is_a_404(ci_client, monkeypatch):
    client, calls = ci_client
    client.app.state.db_pool = _FakePool(slug=None)
    assert client.post(MINT, json={"name": "x"}).status_code == 404
    assert calls["minted"] == []


def test_a_missing_body_is_treated_as_unnamed(ci_client):
    """The route predates the body, and callers that send none must keep
    working rather than 400 on a field they have never heard of."""
    client, _ = ci_client
    r = client.post(MINT)
    assert r.status_code == 201
    assert r.json()["user_sub"] == f"ci:{SLUG}"
