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
    # Separate KV bucket for the worker registry. Empty (the default) keeps the
    # registry in ``kv_bucket`` alongside job rows, which is what every
    # deployment does today.
    #
    # WHY IT IS WORTH SEPARATING. A worker must write arbitrary job ids to
    # report progress, so its credential needs ``$KV.<kv_bucket>.>``. Registry
    # rows live in that same bucket under ``__meta_worker__<id>``, and a NATS
    # subject wildcard matches a WHOLE token -- ``__meta_worker__<id>`` is one
    # token, so the ``>`` that job progress requires already covers every
    # worker's registry row, and no ``deny`` can carve them back out
    # (``__meta_worker__*`` is not a prefix pattern). Any credential that can
    # report job progress can therefore overwrite any worker's registry row,
    # including one claiming a ``plugin_id`` it does not own -- and the API
    # believes what a registry row claims.
    #
    # Moving the registry into its own bucket puts those rows on
    # ``$KV.<registry_kv_bucket>.<worker_id>``, where the id IS the whole token,
    # so a credential can finally be pinned to one worker's own row. That is
    # what ``deploy/worker-trust.md`` proposed and could not express.
    #
    # Opt-in rather than default because switching buckets while workers are
    # running would empty the admin panel for a heartbeat window. See
    # ``JobQueue.list_workers``, which reads both during the changeover.
    registry_kv_bucket: str = ""
    # --- credentials -------------------------------------------------
    # All empty (the default) is an unauthenticated connection, which is
    # what every deployment does today and what a `nats -js` server with
    # no accounts block expects. Set at most one of creds_file /
    # user+password / token / nkey_seed_file; they map straight onto the
    # matching ``nats.connect()`` kwargs.
    #
    # The point of having them is least privilege: once the server has an
    # accounts block, the API connects as a principal that may administer
    # the stream and a worker as one that may only pull its own pool's
    # subject. See ``deploy/worker-trust.md``.
    creds_file: str = ""
    user: str = ""
    password: str = ""
    token: str = ""
    nkey_seed_file: str = ""
    # The nkey seed ITSELF, not a path to it.
    #
    # Both forms exist because secret-injection differs: some systems mount a
    # file, others populate the environment. Without this field the second kind
    # has no route at all — writing the seed to a temp file just to hand back a
    # path would put it on disk for no reason.
    #
    # The naming is asymmetric (`ADA_VIEWER_NATS_NKEY_SEED` is the *file*, and
    # `..._VALUE` is the seed) because the file form shipped first. Flipping the
    # meaning of an env var that is already released would be a silent
    # credential change, which is a worse outcome than an odd pair of names.
    nkey_seed: str = ""
    # PEM bundle for verifying the server certificate. Only needed when
    # the server presents a cert signed by a private CA — with a public
    # CA (or with plaintext) leave it empty and the default trust store /
    # no-TLS path applies.
    tls_ca: str = ""


@dataclass(frozen=True)
class AuthConfig:
    """Provider-agnostic OIDC settings.

    One implementation handles both Authentik (self-hosted) and Azure AD
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
    # Extra OIDC scope(s) to request on top of the always-sent
    # ``openid profile email offline_access`` base. Empty for IdPs that
    # mint an API-audienced token from the base scopes alone (e.g.
    # Authentik). Set to a resource scope (e.g. Azure AD's
    # ``api://<client-id>/access_as_user``) when the provider otherwise
    # issues an access token audienced at something other than this app,
    # so the ``aud`` claim matches ``audience`` above. Space-separated.
    # Defaulted last so existing constructors stay valid.
    scope: str = ""


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
    # Default UI shell id served to the browser via /config.js, letting one
    # image boot into any UI shell it carries. Empty => not configured, and
    # the frontend keeps whatever default was baked in at build time.
    # Defaulted last so existing constructors stay valid.
    ui_default: str = ""


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
        registry_kv_bucket=os.environ.get("ADA_VIEWER_NATS_REGISTRY_KV_BUCKET", "").strip(),
        durable=os.environ.get("ADA_VIEWER_NATS_DURABLE", "ada-viewer-worker"),
        creds_file=os.environ.get("ADA_VIEWER_NATS_CREDS", "").strip(),
        user=os.environ.get("ADA_VIEWER_NATS_USER", "").strip(),
        # Not stripped: a password may legitimately begin or end with
        # whitespace, and silently trimming it turns a working secret
        # into an auth failure nobody can explain.
        password=os.environ.get("ADA_VIEWER_NATS_PASSWORD", ""),
        token=os.environ.get("ADA_VIEWER_NATS_TOKEN", ""),
        nkey_seed_file=os.environ.get("ADA_VIEWER_NATS_NKEY_SEED", "").strip(),
        # Stripped: an nkey seed is base32 with no leading or trailing
        # whitespace, and a secret store that appends a newline is common
        # enough that not stripping would turn a correct secret into an auth
        # failure. (Unlike a password, where the whitespace may be the secret.)
        nkey_seed=os.environ.get("ADA_VIEWER_NATS_NKEY_SEED_VALUE", "").strip(),
        tls_ca=os.environ.get("ADA_VIEWER_NATS_TLS_CA", "").strip(),
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
        scope=os.environ.get("ADA_VIEWER_AUTH_SCOPE", "").strip(),
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
    # Runtime override for the frontend's default UI shell. Unset (or blank)
    # means "not configured" — NOT "the built-in UI" — so an image that was
    # built with a default UI keeps it unless a deployment says otherwise.
    ui_default = os.environ.get("ADA_VIEWER_UI_DEFAULT", "").strip()

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
            ui_default=ui_default,
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
            ui_default=ui_default,
        )

    raise ValueError(f"Unsupported ADA_VIEWER_STORAGE_KIND: {kind!r} (expected 's3' or 'local')")
