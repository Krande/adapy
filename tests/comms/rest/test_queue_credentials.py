"""Credential plumbing and the manage/use split on ``JobQueue.connect``.

Two properties are worth pinning down here.

**Credentials are opt-in and absent by default.** Every deployment today
connects to a `nats -js` with no accounts block, so an empty
``QueueConfig`` must produce a ``nats.connect()`` call with no auth
kwargs at all — adding one (even ``user=""``) would change how the
server treats the connection.

**Workers do not administer JetStream.** ``connect(manage=False)`` must
not create the stream or the bucket; it binds what the API already made.
That is the prerequisite for issuing a worker a credential without
stream-admin rights (deploy/worker-trust.md) — while the worker's first
act was ``add_stream``, no such credential could exist.
"""

import ssl

import pytest
from nats.js.errors import NotFoundError

from ada.comms.rest.config import QueueConfig, load_settings
from ada.comms.rest.queue import JobQueue


def _nkeys_present(monkeypatch) -> None:
    """Assume the optional `nkeys` package is installed.

    The credential forms below map straight onto nats-py kwargs, but two of
    them need `nkeys` to sign the server's nonce, and `_connect_options`
    refuses early when it is absent (see test_queue_transport_deps). `nkeys` is
    PyPI-only and is genuinely missing from some environments, so a test about
    MAPPING states the assumption rather than inheriting it from whichever
    machine runs it.
    """
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **kw: object() if name == "nkeys" else real(name, *a, **kw),
    )


def _cfg(**over) -> QueueConfig:
    base = dict(url="nats://localhost:4222", stream="S", subject="subj", kv_bucket="kv", durable="d")
    base.update(over)
    return QueueConfig(**base)


# --- connect option mapping -----------------------------------------


def test_no_credentials_configured_sends_no_auth_kwargs():
    assert JobQueue(_cfg())._connect_options(None) == {}


def test_connection_name_is_passed_through():
    assert JobQueue(_cfg())._connect_options("adapy-worker-ext-01") == {"name": "adapy-worker-ext-01"}


@pytest.mark.parametrize(
    "field,value,kwarg",
    [
        ("creds_file", "/secrets/worker.creds", "user_credentials"),
        ("nkey_seed_file", "/secrets/worker.nk", "nkeys_seed"),
        ("user", "worker-internal", "user"),
        ("password", "hunter2", "password"),
        ("token", "t0ken", "token"),
    ],
)
def test_each_credential_form_maps_to_its_nats_kwarg(monkeypatch, field, value, kwarg):
    _nkeys_present(monkeypatch)
    opts = JobQueue(_cfg(**{field: value}))._connect_options(None)
    assert opts == {kwarg: value}


def test_tls_ca_becomes_an_ssl_context(tmp_path):
    # load_verify_locations rejects a file with no certificate in it, so
    # the failure mode we assert on is "the CA was actually loaded",
    # not "a context was constructed and the path ignored".
    ca = tmp_path / "ca.pem"
    ca.write_text("not a certificate")
    with pytest.raises(ssl.SSLError):
        JobQueue(_cfg(tls_ca=str(ca)))._connect_options(None)


# --- manage split ----------------------------------------------------


class _FakeJS:
    """Records which JetStream admin calls were attempted."""

    def __init__(self, *, bucket_exists=True):
        self.calls: list[str] = []
        self._bucket_exists = bucket_exists

    async def add_stream(self, *a, **kw):
        self.calls.append("add_stream")

    async def update_stream(self, *a, **kw):
        self.calls.append("update_stream")

    async def delete_consumer(self, *a, **kw):
        self.calls.append("delete_consumer")

    async def create_key_value(self, *a, **kw):
        self.calls.append("create_key_value")
        return "kv-handle"

    async def key_value(self, bucket):
        self.calls.append("key_value")
        if not self._bucket_exists:
            raise NotFoundError()
        return "kv-handle"


class _FakeNC:
    def __init__(self, js):
        self._js = js

    def jetstream(self):
        return self._js


def _patch_connect(monkeypatch, js) -> dict:
    seen: dict = {}

    async def fake_connect(url, **opts):
        seen["url"] = url
        seen["opts"] = opts
        return _FakeNC(js)

    monkeypatch.setattr("ada.comms.rest.queue.nats.connect", fake_connect)
    return seen


@pytest.mark.asyncio
async def test_manage_true_provisions_the_topology(monkeypatch):
    js = _FakeJS()
    _patch_connect(monkeypatch, js)
    q = JobQueue(_cfg())

    await q.connect(manage=True)

    assert "add_stream" in js.calls
    assert "create_key_value" in js.calls


@pytest.mark.asyncio
async def test_manage_false_creates_nothing_and_binds_the_bucket(monkeypatch):
    js = _FakeJS()
    _patch_connect(monkeypatch, js)
    q = JobQueue(_cfg())

    await q.connect(manage=False)

    assert js.calls == ["key_value"]


@pytest.mark.asyncio
async def test_manage_false_raises_a_named_cause_when_the_api_never_appears(monkeypatch):
    js = _FakeJS(bucket_exists=False)
    _patch_connect(monkeypatch, js)
    q = JobQueue(_cfg())
    # Collapse the wait so the test asserts on the give-up behaviour
    # rather than on how long we are willing to wait for it.
    monkeypatch.setattr(JobQueue, "_BIND_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(JobQueue, "_BIND_POLL_SECONDS", 0.0)

    with pytest.raises(RuntimeError, match="created by the API"):
        await q.connect(manage=False)

    # Still no attempt to fix it by provisioning the bucket itself.
    assert "create_key_value" not in js.calls


@pytest.mark.asyncio
async def test_credentials_reach_nats_connect(monkeypatch):
    _nkeys_present(monkeypatch)
    js = _FakeJS()
    seen = _patch_connect(monkeypatch, js)
    q = JobQueue(_cfg(creds_file="/secrets/ext-01.creds"))

    await q.connect(manage=False, name="adapy-worker-ext-01")

    assert seen["opts"] == {"name": "adapy-worker-ext-01", "user_credentials": "/secrets/ext-01.creds"}


# --- settings --------------------------------------------------------


def test_env_populates_the_credential_fields(monkeypatch):
    monkeypatch.setenv("ADA_VIEWER_NATS_URL", "nats://nats:4222")
    monkeypatch.setenv("ADA_VIEWER_NATS_CREDS", "  /secrets/worker.creds  ")
    monkeypatch.setenv("ADA_VIEWER_NATS_USER", "worker-internal")
    monkeypatch.setenv("ADA_VIEWER_NATS_TLS_CA", "/secrets/ca.pem")

    queue = load_settings().queue

    assert queue.creds_file == "/secrets/worker.creds"
    assert queue.user == "worker-internal"
    assert queue.tls_ca == "/secrets/ca.pem"


def test_password_whitespace_is_preserved(monkeypatch):
    # A secret generated by a password manager can legitimately end in a
    # space; trimming it turns a working credential into an auth failure
    # that looks like a server-side problem.
    monkeypatch.setenv("ADA_VIEWER_NATS_PASSWORD", " s3cret ")
    assert load_settings().queue.password == " s3cret "


def test_credentials_default_to_absent(monkeypatch):
    for var in (
        "ADA_VIEWER_NATS_CREDS",
        "ADA_VIEWER_NATS_USER",
        "ADA_VIEWER_NATS_PASSWORD",
        "ADA_VIEWER_NATS_TOKEN",
        "ADA_VIEWER_NATS_NKEY_SEED",
        "ADA_VIEWER_NATS_TLS_CA",
    ):
        monkeypatch.delenv(var, raising=False)

    queue = load_settings().queue

    assert JobQueue(queue)._connect_options(None) == {}
