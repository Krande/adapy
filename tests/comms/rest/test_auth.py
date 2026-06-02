"""Tests for the OIDC verifier + current_user FastAPI dep.

We avoid hitting a real IdP by spinning up a tiny in-process JWKS
issuer: an RSA keypair is generated per test, signs a JWT, and the
verifier's HTTP client is monkey-patched to serve discovery + JWKS
documents from that key. This keeps the suite hermetic and fast (~1
second total).
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ada.comms.rest import auth as auth_module
from ada.comms.rest.auth import User, current_user
from ada.comms.rest.config import AuthConfig

ISSUER = "https://issuer.test"
CLIENT_ID = "ada-viewer-test"
ADMIN_GROUP = "ada-admins"


# ── Helpers: generate a key, expose JWKS, sign a token ─────────────────


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk_from_public(key, kid: str) -> dict:
    pub = key.public_key().public_numbers()
    # Object_store-style: PyJWKClient consumes a standard RFC 7517 JWK.
    import base64

    def _b64(n: int) -> str:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64(pub.n),
        "e": _b64(pub.e),
    }


def _sign(claims: dict, key, kid: str = "k1") -> str:
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def signing_key():
    return _rsa_key()


@pytest.fixture
def base_claims():
    now = int(time.time())
    return {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "user-123",
        "iat": now,
        "exp": now + 3600,
        "email": "alice@example.com",
        "name": "Alice Example",
        "preferred_username": "alice",
        "groups": [ADMIN_GROUP],
    }


@pytest.fixture
def stub_jwks(monkeypatch, signing_key):
    """Make the verifier's JWKS calls return *our* in-memory JWKS doc.

    PyJWKClient does its own urllib fetch, so we monkey-patch
    `urllib.request.urlopen` for the JWKS URL specifically. Discovery
    doc fetch goes via httpx, so we patch the verifier's
    `_discovery_doc` to short-circuit.
    """
    jwk = _jwk_from_public(signing_key, kid="k1")
    jwks_uri = f"{ISSUER}/jwks.json"
    discovery = {"jwks_uri": jwks_uri, "issuer": ISSUER}

    async def _stub_discovery(self):
        return discovery

    monkeypatch.setattr(auth_module._JWKSVerifier, "_discovery_doc", _stub_discovery, raising=True)

    # Patch PyJWKClient's HTTP fetcher.
    import urllib.request

    orig_urlopen = urllib.request.urlopen

    class _Resp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def _stub_urlopen(url, *a, **k):
        target = url.full_url if hasattr(url, "full_url") else url
        print(f"[stub-urlopen] {target!r}")
        if isinstance(target, str) and "jwks.json" in target:
            return _Resp(json.dumps({"keys": [jwk]}).encode())
        return orig_urlopen(url, *a, **k)

    monkeypatch.setattr(urllib.request, "urlopen", _stub_urlopen)
    return jwk


# ── App-level fixture: a small FastAPI app with one gated route ─────────


def _make_app(enabled: bool) -> FastAPI:
    from fastapi import Depends

    app = FastAPI()
    cfg = AuthConfig(
        enabled=enabled,
        issuer=ISSUER,
        client_id=CLIENT_ID,
        audience=CLIENT_ID,
        admin_group=ADMIN_GROUP,
        cli_token_secret="",
    )
    auth_module.install(app, cfg)

    @app.get("/who2")
    async def who(user: User = Depends(current_user)):
        return {
            "sub": user.sub,
            "is_admin": user.is_admin,
            "email": user.email,
            "groups": sorted(user.groups),
        }

    return app


# ── Tests ─────────────────────────────────────────────────────────────


def test_auth_disabled_returns_local_dev_user():
    app = _make_app(enabled=False)
    with TestClient(app) as client:
        r = client.get("/who2")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sub"] == "local-dev"
        assert body["is_admin"] is True


def test_valid_token_yields_authenticated_user(stub_jwks, signing_key, base_claims):
    app = _make_app(enabled=True)
    token = _sign(base_claims, signing_key)
    with TestClient(app) as client:
        r = client.get("/who2", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["sub"] == "user-123"
        assert body["email"] == "alice@example.com"
        assert body["is_admin"] is True
        assert ADMIN_GROUP in body["groups"]


def test_missing_token_yields_401():
    app = _make_app(enabled=True)
    with TestClient(app) as client:
        r = client.get("/who2")
        assert r.status_code == 401
        assert r.headers.get("WWW-Authenticate") == "Bearer"


def test_expired_token_yields_401(stub_jwks, signing_key, base_claims):
    app = _make_app(enabled=True)
    base_claims["exp"] = int(time.time()) - 60
    token = _sign(base_claims, signing_key)
    with TestClient(app) as client:
        r = client.get("/who2", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert "expired" in r.json()["detail"]


def test_wrong_audience_yields_401(stub_jwks, signing_key, base_claims):
    app = _make_app(enabled=True)
    base_claims["aud"] = "some-other-client"
    token = _sign(base_claims, signing_key)
    with TestClient(app) as client:
        r = client.get("/who2", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert "audience" in r.json()["detail"]


def test_wrong_issuer_yields_401(stub_jwks, signing_key, base_claims):
    app = _make_app(enabled=True)
    base_claims["iss"] = "https://evil.example.com"
    token = _sign(base_claims, signing_key)
    with TestClient(app) as client:
        r = client.get("/who2", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401
        assert "issuer" in r.json()["detail"]


def test_non_admin_user_is_not_admin(stub_jwks, signing_key, base_claims):
    app = _make_app(enabled=True)
    base_claims["groups"] = ["random-group"]
    token = _sign(base_claims, signing_key)
    with TestClient(app) as client:
        r = client.get("/who2", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["is_admin"] is False


def test_missing_groups_claim_is_not_admin(stub_jwks, signing_key, base_claims):
    app = _make_app(enabled=True)
    base_claims.pop("groups")
    token = _sign(base_claims, signing_key)
    with TestClient(app) as client:
        r = client.get("/who2", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["is_admin"] is False


def test_garbage_token_yields_401():
    app = _make_app(enabled=True)
    with TestClient(app) as client:
        r = client.get("/who2", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401
