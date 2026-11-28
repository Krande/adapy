from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field


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
    # The subject prefix of this connection's reply inboxes (nats-py's
    # ``inbox_prefix``). Empty keeps nats-py's ``_INBOX``.
    #
    # A remote worker sets its own, ``_INBOX_<worker name>``, so its NATS
    # permissions can allow subscribing to ``_INBOX_<worker name>.>`` and
    # nothing wider: every credential allowed ``_INBOX.>`` can read every other
    # client's replies, job grants included. A subscription to ``_INBOX.>`` does
    # not match ``_INBOX_x.>`` (a wildcard matches whole tokens), so the private
    # prefix also keeps existing keys out of it. See ``deploy/remote-workers.md``.
    inbox_prefix: str = ""


#: The subject a remote worker asks for a job grant on: ``<prefix>.<capability>.<worker name>``.
DEFAULT_GRANT_SUBJECT_PREFIX = "ada.viewer.grants"
#: How long a job grant is valid. A worker refreshes it at two thirds of this.
DEFAULT_GRANT_TTL_S = 15 * 60
#: Above this an upload is a multipart upload; at or below it, one presigned PUT.
DEFAULT_GRANT_PART_SIZE = 64 * 1024 * 1024
#: The largest single object a grant may upload (S3 allows 10 000 parts).
DEFAULT_GRANT_MAX_SIZE = 200 * 1024 * 1024 * 1024
#: The largest body the small-object routes carry through the API (a run record, a marker).
DEFAULT_GRANT_SMALL_OBJECT_MAX = 8 * 1024 * 1024


@dataclass(frozen=True)
class WorkerGrantsConfig:
    """Job grants: per-job, short-lived storage access for remote workers (``job_grants.py``).

    Off unless ``secret`` is set (``ADA_VIEWER_WORKER_GRANT_SECRET``): no responder, no bucket,
    and the ``/api/jobs/{job_id}/storage/...`` routes answer 404. Workers that hold storage keys
    are not affected either way.
    """

    secret: str = ""
    ttl_s: int = DEFAULT_GRANT_TTL_S
    subject_prefix: str = DEFAULT_GRANT_SUBJECT_PREFIX
    #: The KV bucket the job -> worker bindings live in. The API creates it; worker
    #: credentials need no access to it.
    kv_bucket: str = "ada-viewer-job-grants"
    part_size: int = DEFAULT_GRANT_PART_SIZE
    max_size: int = DEFAULT_GRANT_MAX_SIZE
    small_object_max: int = DEFAULT_GRANT_SMALL_OBJECT_MAX
    #: What a remote worker is told to connect to (``ADA_VIEWER_NATS_PUBLIC_URL``). Not secret.
    nats_public_url: str = ""
    #: The oldest ada-py a remote worker should run (``ADA_VIEWER_WORKER_MIN_ADA_PY``), or empty.
    min_ada_py: str = ""
    #: Opaque per-capability settings served to workers as they are
    #: (``ADA_VIEWER_WORKER_BOOTSTRAP_EXTRA``, a JSON object). Never interpreted here.
    bootstrap_extra: dict = field(default_factory=dict)
    #: Extra read prefixes per simulation provider id, set by the deployment
    #: (``ADA_VIEWER_WORKER_GRANT_READ_PREFIXES``, ``{"<provider id>": ["assets/x/"]}``), on top of
    #: what the provider declares in code (``ada.simulation.declare_storage_read_prefixes``).
    read_prefixes: dict = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.secret)


@dataclass(frozen=True)
class GrantStorageConfig:
    """A worker that holds no storage keys (``ADA_VIEWER_STORAGE_KIND=grants``): it asks the API
    for a job grant over NATS and reads and writes through ``/api/jobs/{job_id}/storage/...``."""

    api_url: str
    worker_name: str
    subject_prefix: str = DEFAULT_GRANT_SUBJECT_PREFIX
    request_timeout_s: float = 10.0


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
class SimulationRemote:
    """An Ada Studio this one hands simulation runs to (``ADA_SIMULATION_REMOTE_*``): a studio with
    no simulation workers of its own (``ada dev connections``) lists the remote's providers and
    relays a run on one of them (:mod:`ada.comms.rest.simulation_relay`). ``url`` is the remote's
    origin, ``token`` a bearer for its API (empty: none sent), ``scope`` the remote scope the run's
    files and record live in."""

    url: str
    token: str = ""
    scope: str = "user:me"


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
    # A launcher's per-launch token that enables ``POST /api/local/shutdown``
    # (see local_shutdown.py). Empty — every deployment — means no such route.
    # It also needs a loopback ``host``, so a token that leaked into a deployed
    # environment still enables nothing there.
    local_shutdown_token: str = ""
    # Where runs on a provider this studio does not have are handed off. None (every deployment
    # but a studio set up for it) means they are not.
    simulation_remote: SimulationRemote | None = None
    # Whether an agent may drive a user's open viewer over the REST API (``/api/viewer/...``,
    # routes/viewer_control.py), as one of VIEWER_CONTROL_MODES (ADA_VIEWER_CONTROL,
    # :func:`parse_viewer_control`). "off" by default: the routes answer 404 and no page connects.
    viewer_control: str = "off"
    #: Job grants for remote workers (the API side). Off unless a secret is set.
    worker_grants: WorkerGrantsConfig = field(default_factory=WorkerGrantsConfig)
    #: Set when ``storage_kind == "grants"`` (a worker with no storage keys).
    grant_storage: GrantStorageConfig | None = None
    #: The deployment's default procedural engine, by slug or engine id
    #: (``ADA_DEFAULT_PROCEDURAL_ENGINE``). Empty: adapy's built-in. The
    #: ``procedural_default_engine`` app setting overrides it (procedural_default_engine.py).
    default_procedural_engine: str = ""

    @property
    def viewer_control_enabled(self) -> bool:
        return self.viewer_control != VIEWER_CONTROL_OFF


#: ``ADA_VIEWER_CONTROL``'s modes: off (the default); on, each tab opting in ("opt-in", a deployed
#: server); on, every tab taking part unless its user switches it off ("auto", a local dev stack).
VIEWER_CONTROL_OFF, VIEWER_CONTROL_OPT_IN, VIEWER_CONTROL_AUTO = "off", "opt-in", "auto"
VIEWER_CONTROL_MODES = (VIEWER_CONTROL_OFF, VIEWER_CONTROL_OPT_IN, VIEWER_CONTROL_AUTO)
_VIEWER_CONTROL_VALUES = {
    "": VIEWER_CONTROL_OFF,
    "false": VIEWER_CONTROL_OFF,
    "off": VIEWER_CONTROL_OFF,
    "true": VIEWER_CONTROL_OPT_IN,
    "opt-in": VIEWER_CONTROL_OPT_IN,
    "on": VIEWER_CONTROL_AUTO,
    "auto": VIEWER_CONTROL_AUTO,
}


def parse_viewer_control(raw: str | None) -> str:
    """``ADA_VIEWER_CONTROL`` as a mode: unset, ``false`` or ``off`` -> ``"off"``; ``true`` or
    ``opt-in`` -> ``"opt-in"``; ``on`` or ``auto`` -> ``"auto"``. Anything else is refused rather than
    guessed: a typo must not turn on a way to drive users' viewers."""
    value = (raw or "").strip().lower()
    if value not in _VIEWER_CONTROL_VALUES:
        raise ValueError(
            f"ADA_VIEWER_CONTROL={raw!r}: expected one of off/false (disabled), true/opt-in (each tab "
            "turns it on), on/auto (every tab takes part by default)"
        )
    return _VIEWER_CONTROL_VALUES[value]


def _bool(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _json_object_env(name: str) -> dict:
    """A JSON object from the environment, ``{}`` when unset. Refused, naming the variable, when it
    is set to anything but an object: a typo in a deployment setting must not read as "nothing"."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object, got {type(value).__name__}")
    return value


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def load_worker_grants_config() -> WorkerGrantsConfig:
    """The API's job-grant settings (``ADA_VIEWER_WORKER_GRANT_*``, ``ADA_VIEWER_NATS_PUBLIC_URL``,
    ``ADA_VIEWER_WORKER_BOOTSTRAP_EXTRA``, ``ADA_VIEWER_WORKER_MIN_ADA_PY``)."""
    part_size = _int_env("ADA_VIEWER_WORKER_GRANT_PART_SIZE", DEFAULT_GRANT_PART_SIZE)
    if part_size < 5 * 1024 * 1024:
        # S3 refuses a multipart part (other than the last) below 5 MiB.
        raise ValueError("ADA_VIEWER_WORKER_GRANT_PART_SIZE must be at least 5 MiB (5242880)")
    read_prefixes = _json_object_env("ADA_VIEWER_WORKER_GRANT_READ_PREFIXES")
    for provider_id, prefixes in read_prefixes.items():
        if not isinstance(prefixes, list) or not all(isinstance(p, str) for p in prefixes):
            raise ValueError(f"ADA_VIEWER_WORKER_GRANT_READ_PREFIXES[{provider_id!r}] must be a list of key prefixes")
    return WorkerGrantsConfig(
        # Stripped like the nkey seed: a secret store that appends a newline is common, and an
        # HMAC key with a stray newline signs tokens nothing else can verify.
        secret=os.environ.get("ADA_VIEWER_WORKER_GRANT_SECRET", "").strip(),
        ttl_s=_int_env("ADA_VIEWER_WORKER_GRANT_TTL_S", DEFAULT_GRANT_TTL_S),
        subject_prefix=os.environ.get("ADA_VIEWER_WORKER_GRANT_SUBJECT_PREFIX", "").strip().strip(".")
        or DEFAULT_GRANT_SUBJECT_PREFIX,
        kv_bucket=os.environ.get("ADA_VIEWER_WORKER_GRANT_KV_BUCKET", "").strip() or "ada-viewer-job-grants",
        part_size=part_size,
        max_size=_int_env("ADA_VIEWER_WORKER_GRANT_MAX_SIZE", DEFAULT_GRANT_MAX_SIZE),
        small_object_max=_int_env("ADA_VIEWER_WORKER_GRANT_SMALL_OBJECT_MAX", DEFAULT_GRANT_SMALL_OBJECT_MAX),
        nats_public_url=os.environ.get("ADA_VIEWER_NATS_PUBLIC_URL", "").strip(),
        min_ada_py=os.environ.get("ADA_VIEWER_WORKER_MIN_ADA_PY", "").strip(),
        bootstrap_extra=_json_object_env("ADA_VIEWER_WORKER_BOOTSTRAP_EXTRA"),
        read_prefixes=read_prefixes,
    )


#: What a worker name may be: one NATS subject token, and a readable one.
WORKER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,62}$")


def worker_name_from_env() -> str:
    """This worker's name: ``ADA_VIEWER_WORKER_NAME``, else ``HOSTNAME`` (the worker id).

    It is a token of the grant subject (``<prefix>.<capability>.<name>``) and of the private inbox
    (``_INBOX_<name>``), so the NATS permissions of the worker's key pin both to it. Refused when it
    is not one subject token: a ``.`` in it would make a deeper subject the key is not allowed."""
    name = (os.environ.get("ADA_VIEWER_WORKER_NAME") or os.environ.get("HOSTNAME") or "").strip()
    if not name:
        import socket

        name = socket.gethostname().split(".")[0]
    if not WORKER_NAME_PATTERN.match(name):
        raise ValueError(
            f"worker name {name!r} is not usable as a NATS subject token: set ADA_VIEWER_WORKER_NAME to "
            "letters, digits, '-' and '_' (at most 63 characters)"
        )
    return name


def worker_storage_kind(env=None) -> str:
    """The storage kind a WORKER runs with when ``ADA_VIEWER_STORAGE_KIND`` is not set.

    ``grants`` when the worker has the API's URL and no storage of its own configured (no
    ``ADA_VIEWER_S3_BUCKET``, no ``ADA_VIEWER_LOCAL_PATH``); ``local`` otherwise, as before. A set
    ``ADA_VIEWER_STORAGE_KIND`` always wins, so a worker with S3 settings is never moved: the rule
    only picks for a worker that would otherwise have had a local store it was never given."""
    env = os.environ if env is None else env
    explicit = (env.get("ADA_VIEWER_STORAGE_KIND") or "").strip().lower()
    if explicit:
        return explicit
    has_api = bool((env.get("ADA_VIEWER_API_URL") or "").strip())
    has_store = bool((env.get("ADA_VIEWER_S3_BUCKET") or "").strip()) or bool(
        (env.get("ADA_VIEWER_LOCAL_PATH") or "").strip()
    )
    return "grants" if has_api and not has_store else "local"


def load_settings(*, role: str = "api") -> Settings:
    """The settings from the environment. ``role="worker"`` lets an unset
    ``ADA_VIEWER_STORAGE_KIND`` pick ``grants`` (:func:`worker_storage_kind`); the API never does."""
    if role == "worker":
        kind = worker_storage_kind()
    else:
        kind = os.environ.get("ADA_VIEWER_STORAGE_KIND", "local").strip().lower()
    host = os.environ.get("ADA_VIEWER_HOST", "0.0.0.0")
    port = int(os.environ.get("ADA_VIEWER_PORT", "8080"))
    static_path = os.environ.get("ADA_VIEWER_STATIC_PATH", "").strip()
    if not static_path:
        # An installed viewer bundle (ADA_VIEWER_BUNDLE), plugins built in; an explicit
        # ADA_VIEWER_STATIC_PATH still wins.
        from ada.comms.viewer_bundles import resolve_viewer_bundle

        bundle = resolve_viewer_bundle()
        if bundle is not None:
            if bundle.dist is None:
                raise ValueError(f'viewer bundle "{bundle.name}" has no hosted build ({bundle.root / "dist"})')
            static_path = str(bundle.dist)

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
        inbox_prefix=os.environ.get("ADA_VIEWER_NATS_INBOX_PREFIX", "").strip(),
    )
    worker_grants = load_worker_grants_config()

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
    local_shutdown_token = os.environ.get("ADA_VIEWER_LOCAL_SHUTDOWN_TOKEN", "").strip()
    viewer_control = parse_viewer_control(os.environ.get("ADA_VIEWER_CONTROL"))
    # The engine a new procedural model gets when nobody names one (procedural_default_engine.py).
    default_procedural_engine = os.environ.get("ADA_DEFAULT_PROCEDURAL_ENGINE", "").strip()
    remote_url = os.environ.get("ADA_SIMULATION_REMOTE_URL", "").strip().rstrip("/")
    simulation_remote = (
        SimulationRemote(
            url=remote_url,
            token=os.environ.get("ADA_SIMULATION_REMOTE_TOKEN", "").strip(),
            scope=os.environ.get("ADA_SIMULATION_REMOTE_SCOPE", "").strip() or "user:me",
        )
        if remote_url
        else None
    )

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
            local_shutdown_token=local_shutdown_token,
            simulation_remote=simulation_remote,
            viewer_control=viewer_control,
            worker_grants=worker_grants,
            default_procedural_engine=default_procedural_engine,
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
            local_shutdown_token=local_shutdown_token,
            simulation_remote=simulation_remote,
            viewer_control=viewer_control,
            worker_grants=worker_grants,
            default_procedural_engine=default_procedural_engine,
        )

    if kind == "grants":
        api_url = os.environ.get("ADA_VIEWER_API_URL", "").strip().rstrip("/")
        if not api_url.startswith(("http://", "https://")):
            raise ValueError(
                "ADA_VIEWER_STORAGE_KIND=grants needs ADA_VIEWER_API_URL, the http(s) URL of the API "
                f"that grants storage access (got {api_url!r})"
            )
        grant_storage = GrantStorageConfig(
            api_url=api_url,
            worker_name=worker_name_from_env(),
            subject_prefix=os.environ.get("ADA_VIEWER_WORKER_GRANT_SUBJECT_PREFIX", "").strip().strip(".")
            or DEFAULT_GRANT_SUBJECT_PREFIX,
        )
        return Settings(
            storage_kind="grants",
            s3=None,
            local=None,
            host=host,
            port=port,
            static_path=static_path,
            queue=queue,
            auth=auth,
            database_url=database_url,
            ui_default=ui_default,
            local_shutdown_token=local_shutdown_token,
            simulation_remote=simulation_remote,
            viewer_control=viewer_control,
            worker_grants=worker_grants,
            default_procedural_engine=default_procedural_engine,
            grant_storage=grant_storage,
        )

    raise ValueError(f"Unsupported ADA_VIEWER_STORAGE_KIND: {kind!r} (expected 's3', 'local' or 'grants')")
