"""OIDC JWT verification for the hosted REST viewer.

Provider-agnostic: a single implementation serves both Authentik
(homelab) and Azure AD direct (enterprise). Both expose a
``.well-known/openid-configuration`` discovery doc and a JWKS
endpoint, so configuration is purely env-driven (issuer, client_id,
audience, admin_group).

The :func:`current_user` FastAPI dependency is the public surface:

* When ``AuthConfig.enabled`` is False, it returns a synthetic
  ``local-dev`` :class:`User` with admin rights — keeps local dev and
  the desktop entry path completely untouched.
* When True, it pulls the bearer token from ``Authorization``,
  verifies the signature against the issuer's JWKS (cached, with key
  rotation handled on a ``kid`` cache miss), checks ``iss`` / ``aud``
  / ``exp``, and builds a :class:`User` from the claims.

Group membership is matched as exact strings; that lets the same
``admin_group`` env var hold an Authentik group name *or* an Azure AD
group object id without code branches.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from jwt import PyJWKClient

from ada.config import logger

from .config import AuthConfig

# Discovery + JWKS cache TTL. Authentik / Azure rotate keys
# infrequently; 10 minutes is a sweet spot between freshness and
# request volume to the IdP.
_DISCOVERY_TTL = 600
_JWKS_TTL = 600

# Subset of OIDC claims we actually use. Keeping it narrow makes the
# unit tests simple — we only need to fabricate these fields.
_CLAIM_GROUPS = "groups"

# Self-issued CLI bearer tokens use a fixed `iss` so the verify path
# can route them to HS256 verification with our local secret instead of
# the IdP's JWKS. 30-day TTL — long enough for a debugging session
# without a browser round-trip, short enough that a leaked token isn't
# forever.
_CLI_TOKEN_ISS = "ada-viewer-cli"
_CLI_TOKEN_TTL_SECONDS = 30 * 86400


def _revoke_setting_key(sub: str) -> str:
    return f"cli_token_revoke_at:{sub}"


@dataclass(frozen=True)
class User:
    """Authenticated principal extracted from a verified JWT."""

    sub: str
    email: str
    display_name: str
    groups: frozenset[str]
    is_admin: bool

    @classmethod
    def local_dev(cls) -> "User":
        """Synthetic principal used when auth is disabled (dev / desktop).

        Admin so any future admin-gated endpoints stay reachable from
        the developer's local machine without ceremony.
        """
        return cls(
            sub="local-dev",
            email="local@dev.invalid",
            display_name="Local Dev",
            groups=frozenset(),
            is_admin=True,
        )


class TokenError(HTTPException):
    """401 with ``WWW-Authenticate: Bearer`` so browsers / fetch retry
    via the SPA's auth flow rather than caching a broken state."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            status_code=401,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class _JWKSVerifier:
    """Fetches issuer metadata + JWKS, caches both, refreshes on miss.

    One instance per AuthConfig; held on the FastAPI app state and
    closed on shutdown. Concurrency-safe via a single asyncio.Lock per
    cache slot — concurrent requests during a key rotation event won't
    fan out N parallel JWKS fetches.
    """

    def __init__(self, config: AuthConfig) -> None:
        self._config = config
        self._http = httpx.AsyncClient(timeout=10.0)
        self._discovery: dict[str, Any] | None = None
        self._discovery_at = 0.0
        self._jwks_client: PyJWKClient | None = None
        self._jwks_at = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _discovery_doc(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._discovery and now - self._discovery_at < _DISCOVERY_TTL:
            return self._discovery
        # Issuer is preserved verbatim for `iss` exact-compare during decode;
        # strip any trailing slash here so the discovery URL doesn't double up.
        url = f"{self._config.issuer.rstrip('/')}/.well-known/openid-configuration"
        r = await self._http.get(url)
        r.raise_for_status()
        self._discovery = r.json()
        self._discovery_at = now
        return self._discovery

    async def _jwks(self, force_refresh: bool = False) -> PyJWKClient:
        now = time.monotonic()
        if not force_refresh and self._jwks_client is not None and now - self._jwks_at < _JWKS_TTL:
            return self._jwks_client
        async with self._lock:
            # Recheck under the lock — another task may have refreshed
            # while we were waiting.
            if not force_refresh and self._jwks_client is not None and time.monotonic() - self._jwks_at < _JWKS_TTL:
                return self._jwks_client
            doc = await self._discovery_doc()
            jwks_uri = doc["jwks_uri"]
            # PyJWKClient does its own HTTP via urllib; that's fine for
            # a once-per-TTL fetch. Caching layer stays in *this* class.
            self._jwks_client = PyJWKClient(jwks_uri)
            self._jwks_at = time.monotonic()
            return self._jwks_client

    async def verify(self, token: str) -> dict[str, Any]:
        """Validate signature + iss/aud/exp; return the claims dict.

        On a ``kid`` miss (signing key rotated), refresh JWKS once and
        retry — covers the rotation race without a permanent 401.
        """
        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise TokenError(f"malformed token: {exc}") from exc

        for force in (False, True):
            jwks_client = await self._jwks(force_refresh=force)
            try:
                signing_key = jwks_client.get_signing_key_from_jwt(token).key
            except jwt.PyJWKClientError:
                if not force:
                    continue  # rotate-and-retry once
                raise TokenError("signing key not found in JWKS")
            break

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=[unverified_header.get("alg", "RS256")],
                audience=self._config.audience or self._config.client_id,
                issuer=self._config.issuer,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise TokenError("token expired")
        except jwt.InvalidAudienceError:
            raise TokenError("audience mismatch")
        except jwt.InvalidIssuerError:
            raise TokenError("issuer mismatch")
        except jwt.InvalidTokenError as exc:
            raise TokenError(f"invalid token: {exc}") from exc
        return claims


def _claims_to_user(claims: dict[str, Any], admin_group: str) -> User:
    raw_groups = claims.get(_CLAIM_GROUPS) or []
    if not isinstance(raw_groups, (list, tuple, set, frozenset)):
        # Some IdPs ship a single-string `groups` claim. Normalise.
        raw_groups = [raw_groups]
    groups = frozenset(str(g) for g in raw_groups)
    is_admin = bool(admin_group) and admin_group in groups
    return User(
        sub=str(claims["sub"]),
        # Authentik populates `email`; Azure AD v2 prefers `preferred_username`
        # which often *is* the email. Display name follows `name` then falls
        # back to either of the above.
        email=str(claims.get("email") or claims.get("preferred_username") or ""),
        display_name=str(
            claims.get("name") or claims.get("preferred_username") or claims.get("email") or claims.get("sub")
        ),
        groups=groups,
        is_admin=is_admin,
    )


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise TokenError("missing bearer token")
    return token


def install(app, config: AuthConfig) -> None:
    """Attach a verifier to the FastAPI app state and register cleanup.

    Called once from :func:`create_app`. The dep below pulls the
    verifier off ``request.app.state`` so tests can build their own
    ``FastAPI()`` without having to fake module-level state.

    Cleanup runs via the existing app lifespan (which create_app sets
    up for queue connect/close). When the test harness builds a bare
    ``FastAPI()`` without a lifespan, the verifier still works — we
    just don't get an explicit shutdown hook, and the httpx client is
    GC'd when the verifier is dropped.
    """
    if config.enabled:
        verifier = _JWKSVerifier(config)
        app.state.auth_verifier = verifier
    else:
        app.state.auth_verifier = None
        logger.info("auth: disabled (ADA_VIEWER_AUTH_ENABLED=false)")
    app.state.auth_config = config


async def aclose(app) -> None:
    """Release the JWKS verifier's HTTP client. Hook into your lifespan."""
    verifier = getattr(app.state, "auth_verifier", None)
    if verifier is not None:
        await verifier.aclose()


async def current_user(request: Request) -> User:
    """FastAPI dep: returns the authenticated User, or 401s.

    Bypasses validation entirely when auth is disabled — the synthetic
    ``local-dev`` user keeps every endpoint reachable in dev without
    ceremony.

    Two token shapes are accepted: short-lived OIDC access tokens
    (verified via the IdP's JWKS) and long-lived CLI tokens we issue
    ourselves (HS256 with the configured secret). The `iss` claim
    routes between them — peeking at it unverified is safe because
    every shape is then signature-checked.
    """
    config: AuthConfig = request.app.state.auth_config
    if not config.enabled:
        return User.local_dev()
    token = _bearer_token(request)
    try:
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"malformed token: {exc}") from exc
    if unverified.get("iss") == _CLI_TOKEN_ISS:
        return await _verify_cli_token(request, token, config)
    verifier: _JWKSVerifier = request.app.state.auth_verifier
    claims = await verifier.verify(token)
    return _claims_to_user(claims, config.admin_group)


async def _verify_cli_token(request: Request, token: str, config: AuthConfig) -> User:
    if not config.cli_token_secret:
        raise TokenError("CLI tokens disabled on this server")
    try:
        claims = jwt.decode(
            token,
            config.cli_token_secret,
            algorithms=["HS256"],
            issuer=_CLI_TOKEN_ISS,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise TokenError("token expired")
    except jwt.InvalidIssuerError:
        raise TokenError("issuer mismatch")
    except jwt.InvalidTokenError as exc:
        raise TokenError(f"invalid token: {exc}") from exc

    # Per-user "revoke all" cutoff lives in app_settings; tokens minted
    # before the cutoff are rejected. Pool may be absent in shared-only
    # mode — without a DB there's nowhere to store the cutoff, so we
    # fall through (the secret rotation is the operator's escape hatch).
    pool = getattr(request.app.state, "db_pool", None)
    if pool is not None:
        from . import db as db_module

        raw = await db_module.get_setting(pool, _revoke_setting_key(str(claims["sub"])))
        if raw:
            try:
                revoke_at = int(raw)
            except ValueError:
                revoke_at = 0
            if int(claims["iat"]) < revoke_at:
                raise TokenError("token revoked")

    raw_groups = claims.get(_CLAIM_GROUPS) or []
    if not isinstance(raw_groups, (list, tuple, set, frozenset)):
        raw_groups = [raw_groups]
    return User(
        sub=str(claims["sub"]),
        email=str(claims.get("email") or ""),
        display_name=str(claims.get("name") or claims.get("email") or claims["sub"]),
        groups=frozenset(str(g) for g in raw_groups),
        is_admin=bool(claims.get("is_admin")),
    )


def mint_cli_token(user: User, config: AuthConfig) -> tuple[str, int]:
    """Issue a self-signed bearer for CLI / pixi-task use.

    Returns ``(token, exp_unix)``. The caller is expected to display
    the token once and not persist it server-side — this is a
    stateless JWT, not a row in a PAT table. Revocation runs through
    :func:`_revoke_setting_key` and the per-user cutoff in
    ``app_settings``.
    """
    if not config.cli_token_secret:
        raise HTTPException(
            status_code=503,
            detail="CLI tokens disabled (ADA_VIEWER_CLI_TOKEN_SECRET unset)",
        )
    now = int(time.time())
    exp = now + _CLI_TOKEN_TTL_SECONDS
    payload = {
        "iss": _CLI_TOKEN_ISS,
        "sub": user.sub,
        "email": user.email,
        "name": user.display_name,
        _CLAIM_GROUPS: sorted(user.groups),
        "is_admin": user.is_admin,
        "iat": now,
        "exp": exp,
    }
    token = jwt.encode(payload, config.cli_token_secret, algorithm="HS256")
    return token, exp


async def revoke_cli_tokens(pool, user: User) -> int:
    """Bump the per-user revoke cutoff so all previously-minted CLI
    tokens for ``user`` start failing verification on the next use.

    Returns the cutoff unix timestamp. Idempotent — calling twice in
    quick succession just moves the bar a little higher.
    """
    from . import db as db_module

    now = int(time.time())
    await db_module.set_setting(pool, _revoke_setting_key(user.sub), str(now), updated_by=user.sub)
    return now


async def require_admin(user: User = Depends(current_user)) -> User:
    """Compose with :func:`current_user` for admin-only routes."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user
