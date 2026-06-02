from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class S3Config:
    bucket: str
    endpoint: str | None
    # Optional public-facing endpoint used ONLY for presigned URL minting.
    # When the API process talks to the object store over an in-cluster
    # hostname (e.g. ``http://garage.garage.svc.cluster.local:3900``) but
    # the browser must reach the same store over a public HTTPS URL,
    # the presigned URL needs the public hostname or two things break:
    # (1) Mixed Content blocks the HTTPS page from PUTting to http://;
    # (2) the cluster-local DNS name doesn't resolve from the browser.
    # Leave None (or equal to ``endpoint``) for deployments where the
    # same endpoint reaches both sides.
    endpoint_public: str | None
    region: str
    access_key_id: str | None
    secret_access_key: str | None
    prefix: str
    # Garage and MinIO use path-style addressing; AWS uses virtual-hosted.
    virtual_hosted_style: bool


@dataclass(frozen=True)
class LocalConfig:
    path: str
    prefix: str


@dataclass(frozen=True)
class QueueConfig:
    # NATS server URL. When None, conversion endpoints are disabled and
    # the API serves only listing / direct-blob fetches.
    url: str | None
    stream: str
    subject: str
    kv_bucket: str
    durable: str


@dataclass(frozen=True)
class AuthConfig:
    """Provider-agnostic OIDC settings.

    One implementation handles both Authentik (homelab) and Azure AD
    direct (enterprise) — both expose a `.well-known/openid-configuration`
    discovery doc and a JWKS endpoint.

    `enabled=False` (the default) disables every check: the FastAPI
    dep returns a synthetic local user. This keeps dev + the desktop
    code path untouched.

    `audience` falls back to `client_id` when blank — Authentik issues
    tokens with `aud == client_id`, while Azure AD's v2.0 endpoint can
    split the two.

    `admin_group` is matched against the token's `groups` claim. Use a
    group *name* for Authentik (e.g. ``ada-viewer-admins``) and a group
    *object id* for Azure AD; the comparison is exact-string either
    way.
    """

    enabled: bool
    issuer: str
    client_id: str
    audience: str
    admin_group: str
    # Shared secret used to sign long-lived CLI tokens (HS256). Empty
    # disables the mint endpoint so deployments without it don't
    # accidentally hand out 30-day bearers signed with a default key.
    cli_token_secret: str


@dataclass(frozen=True)
class Settings:
    storage_kind: str  # "s3" | "local"
    s3: S3Config | None
    local: LocalConfig | None
    host: str
    port: int
    # Optional path on disk to a built frontend bundle (index.html + assets/).
    # When set, the API also serves the SPA. Empty disables static serving.
    static_path: str
    queue: QueueConfig
    auth: AuthConfig
    # Optional Postgres connection string. When empty the REST viewer
    # runs in shared-only mode (no projects, no admin panel, no audit
    # log) so the helm chart's ``postgres.enabled: false`` path stays
    # functional for tiny deployments.
    database_url: str


def _bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    kind = os.environ.get("ADA_VIEWER_STORAGE_KIND", "local").strip().lower()
    host = os.environ.get("ADA_VIEWER_HOST", "0.0.0.0")
    port = int(os.environ.get("ADA_VIEWER_PORT", "8080"))
    static_path = os.environ.get("ADA_VIEWER_STATIC_PATH", "").strip()

    nats_url = os.environ.get("ADA_VIEWER_NATS_URL", "").strip() or None
    queue = QueueConfig(
        url=nats_url,
        stream=os.environ.get("ADA_VIEWER_NATS_STREAM", "ADA_VIEWER_JOBS"),
        subject=os.environ.get("ADA_VIEWER_NATS_SUBJECT", "ada.viewer.jobs.convert"),
        kv_bucket=os.environ.get("ADA_VIEWER_NATS_KV_BUCKET", "ada-viewer-jobs"),
        durable=os.environ.get("ADA_VIEWER_NATS_DURABLE", "ada-viewer-worker"),
    )

    auth_enabled = _bool(os.environ.get("ADA_VIEWER_AUTH_ENABLED"), default=False)
    auth_client_id = os.environ.get("ADA_VIEWER_AUTH_CLIENT_ID", "").strip()
    # Whatever the operator sets is compared exact-string against the `iss`
    # claim by PyJWT — IdPs differ on trailing slash (Authentik includes
    # one, Azure AD doesn't), so don't normalize here.
    auth_issuer = os.environ.get("ADA_VIEWER_AUTH_ISSUER", "").strip()
    auth_audience = os.environ.get("ADA_VIEWER_AUTH_AUDIENCE", "").strip() or auth_client_id
    auth = AuthConfig(
        enabled=auth_enabled,
        issuer=auth_issuer,
        client_id=auth_client_id,
        audience=auth_audience,
        admin_group=os.environ.get("ADA_VIEWER_AUTH_ADMIN_GROUP", "").strip(),
        cli_token_secret=os.environ.get("ADA_VIEWER_CLI_TOKEN_SECRET", "").strip(),
    )
    if auth.enabled and (not auth.issuer or not auth.client_id):
        raise ValueError(
            "ADA_VIEWER_AUTH_ENABLED=true requires ADA_VIEWER_AUTH_ISSUER " "and ADA_VIEWER_AUTH_CLIENT_ID to be set"
        )

    # Standard env name (DATABASE_URL) so the viewer plays nicely with
    # operators / sub-charts that already inject it (Bitnami Postgres,
    # CNPG, Render, etc.). Empty → shared-only mode.
    database_url = os.environ.get("DATABASE_URL", "").strip()

    if kind == "s3":
        s3 = S3Config(
            bucket=os.environ["ADA_VIEWER_S3_BUCKET"],
            endpoint=os.environ.get("ADA_VIEWER_S3_ENDPOINT"),
            endpoint_public=os.environ.get("ADA_VIEWER_S3_ENDPOINT_PUBLIC", "").strip() or None,
            region=os.environ.get("ADA_VIEWER_S3_REGION", "us-east-1"),
            access_key_id=os.environ.get("ADA_VIEWER_S3_ACCESS_KEY_ID"),
            secret_access_key=os.environ.get("ADA_VIEWER_S3_SECRET_ACCESS_KEY"),
            prefix=os.environ.get("ADA_VIEWER_S3_PREFIX", "").strip("/"),
            virtual_hosted_style=_bool(os.environ.get("ADA_VIEWER_S3_VIRTUAL_HOSTED_STYLE"), default=False),
        )
        return Settings(
            storage_kind="s3",
            s3=s3,
            local=None,
            host=host,
            port=port,
            static_path=static_path,
            queue=queue,
            auth=auth,
            database_url=database_url,
        )

    if kind == "local":
        local = LocalConfig(
            path=os.environ.get("ADA_VIEWER_LOCAL_PATH", "./viewer-data"),
            prefix=os.environ.get("ADA_VIEWER_LOCAL_PREFIX", "").strip("/"),
        )
        return Settings(
            storage_kind="local",
            s3=None,
            local=local,
            host=host,
            port=port,
            static_path=static_path,
            queue=queue,
            auth=auth,
            database_url=database_url,
        )

    raise ValueError(f"Unsupported ADA_VIEWER_STORAGE_KIND: {kind!r} (expected 's3' or 'local')")
