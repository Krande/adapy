"""Tests for the Scope type + authorization helpers."""

from __future__ import annotations

import pytest

from ada.comms.rest.auth import User
from ada.comms.rest.scope import Scope, can_access


def test_shared_scope_has_no_id():
    s = Scope.shared()
    assert s.kind == "shared"
    assert s.id is None
    assert s.prefix() == "shared"


def test_user_scope_requires_id():
    with pytest.raises(ValueError):
        Scope(kind="user", id=None)
    with pytest.raises(ValueError):
        Scope(kind="user", id="")


def test_shared_scope_rejects_id():
    with pytest.raises(ValueError):
        Scope(kind="shared", id="something")


def test_project_scope_prefix():
    s = Scope.project("abc-123")
    assert s.prefix() == "projects/abc-123"


def test_user_scope_prefix():
    s = Scope.user("user-7")
    assert s.prefix() == "users/user-7"


def _user(sub: str, is_admin: bool = False) -> User:
    return User(
        sub=sub,
        email="x@y.z",
        display_name="Test",
        groups=frozenset(),
        is_admin=is_admin,
    )


@pytest.mark.asyncio
async def test_shared_is_open_to_any_authenticated_user():
    assert await can_access(_user("alice"), Scope.shared()) is True


@pytest.mark.asyncio
async def test_user_scope_only_accessible_to_owner():
    alice = _user("alice")
    assert await can_access(alice, Scope.user("alice")) is True
    assert await can_access(alice, Scope.user("bob")) is False


@pytest.mark.asyncio
async def test_admin_does_not_get_implicit_cross_tenant():
    """Admin status grants the admin panel, not casual cross-tenant
    access. Cross-tenant browsing must go through explicit admin
    routes that audit (phase 3)."""
    admin = _user("admin", is_admin=True)
    assert await can_access(admin, Scope.user("bob")) is False


@pytest.mark.asyncio
async def test_project_scope_blocked_without_db():
    """No-DB deployments don't have project memberships, so project
    scopes are categorically inaccessible."""
    alice = _user("alice")
    assert await can_access(alice, Scope.project("any-project"), db_pool=None) is False
