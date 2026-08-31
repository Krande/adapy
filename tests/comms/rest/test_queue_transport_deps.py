"""Connection options that need a package nats-py does not declare.

``nats-py`` imports ``aiohttp`` (WebSocket transport) and ``nkeys`` (nkey and
credentials-file auth) lazily, deep inside connect, and declares neither. So an
environment can be perfectly healthy and still be unable to do either — and the
way the operator finds out is a bare ``ImportError`` from inside a library they
did not configure, naming neither the setting they set nor the package to
install.

These tests pin the two things that fixes: the check happens before any socket
is opened, and the message names both.

The second half covers the inline nkey seed. Secret-injection systems differ —
some mount a file, some populate the environment — and without the inline form
the second kind has no route at all.
"""

import pytest

from ada.comms.rest.config import QueueConfig, load_settings
from ada.comms.rest.queue import JobQueue, MissingTransportDependency


def _cfg(**over) -> QueueConfig:
    base = dict(url="nats://localhost:4222", stream="S", subject="subj", kv_bucket="kv", durable="d")
    base.update(over)
    return QueueConfig(**base)


def _present(monkeypatch, package: str) -> None:
    """Make one package look installed.

    Needed because `nkeys` is PyPI-only and genuinely absent from the test
    environment — so without this the preflight (correctly) fires in the tests
    that are about option MAPPING rather than about the dependency check.
    """
    import importlib.util

    real = importlib.util.find_spec

    def fake(name, *a, **kw):
        if name == package:
            return object()
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


def _hide(monkeypatch, package: str) -> None:
    """Make one package look absent without touching the real environment."""
    import importlib.util

    real = importlib.util.find_spec

    def fake(name, *a, **kw):
        if name == package:
            return None
        return real(name, *a, **kw)

    monkeypatch.setattr(importlib.util, "find_spec", fake)


# --- the checks fire ------------------------------------------------------


def test_a_websocket_url_without_aiohttp_is_refused_by_name(monkeypatch):
    _hide(monkeypatch, "aiohttp")

    with pytest.raises(MissingTransportDependency) as exc:
        JobQueue(_cfg(url="wss://bus.example.com/nats"))._connect_options(None)

    message = str(exc.value)
    assert "ADA_VIEWER_NATS_URL" in message  # the setting they set
    assert "aiohttp" in message  # the package to install


@pytest.mark.parametrize("scheme", ["ws", "wss", "WSS", "Ws"])
def test_the_websocket_check_is_driven_by_the_url_scheme(monkeypatch, scheme):
    # The URL is what selects the transport, so that is what must be inspected
    # — not any credential setting.
    _hide(monkeypatch, "aiohttp")

    with pytest.raises(MissingTransportDependency):
        JobQueue(_cfg(url=f"{scheme}://bus.example.com/nats"))._connect_options(None)


def test_a_plain_nats_url_does_not_need_aiohttp(monkeypatch):
    _hide(monkeypatch, "aiohttp")

    assert JobQueue(_cfg(url="nats://localhost:4222"))._connect_options(None) == {}


@pytest.mark.parametrize(
    "field,setting",
    [
        ("creds_file", "ADA_VIEWER_NATS_CREDS"),
        ("nkey_seed_file", "ADA_VIEWER_NATS_NKEY_SEED"),
        ("nkey_seed", "ADA_VIEWER_NATS_NKEY_SEED_VALUE"),
    ],
)
def test_nkey_shaped_auth_without_nkeys_is_refused_by_name(monkeypatch, field, setting):
    # A .creds file carries a JWT *and* a seed, so signing needs nkeys just as
    # the bare-seed forms do. Missing it there is the same failure.
    _hide(monkeypatch, "nkeys")

    with pytest.raises(MissingTransportDependency) as exc:
        JobQueue(_cfg(**{field: "x"}))._connect_options(None)

    assert setting in str(exc.value)
    assert "nkeys" in str(exc.value)


def test_user_password_needs_no_optional_package(monkeypatch):
    # The one auth form with no lazy import behind it. Worth pinning: it is the
    # fallback if the pypi/conda mix for nkeys is ever unacceptable.
    _hide(monkeypatch, "nkeys")
    _hide(monkeypatch, "aiohttp")

    opts = JobQueue(_cfg(user="worker", password="s3cret"))._connect_options(None)

    assert opts == {"user": "worker", "password": "s3cret"}


def test_the_check_runs_before_any_connection_is_attempted(monkeypatch):
    """The point of checking here rather than letting nats-py fail.

    `_connect_options` is pure — it opens nothing. If this ever moved after the
    dial, a misconfigured worker would spend its connect timeout before saying
    anything useful.
    """
    _hide(monkeypatch, "aiohttp")
    dialled = []
    monkeypatch.setattr(
        "ada.comms.rest.queue.nats.connect",
        lambda *a, **kw: dialled.append(a) or None,
    )

    with pytest.raises(MissingTransportDependency):
        JobQueue(_cfg(url="wss://bus.example.com"))._connect_options(None)

    assert dialled == []


# --- the inline seed ------------------------------------------------------


def test_an_inline_seed_is_passed_as_a_value_not_a_path(monkeypatch):
    _present(monkeypatch, "nkeys")
    opts = JobQueue(_cfg(nkey_seed="SUAWKEY"))._connect_options(None)

    # nkeys_seed_str, not nkeys_seed: handing nats-py a seed where it expects a
    # path would have it try to open a file named after the secret.
    assert opts == {"nkeys_seed_str": "SUAWKEY"}


def test_a_seed_file_is_still_passed_as_a_path(monkeypatch):
    _present(monkeypatch, "nkeys")
    assert JobQueue(_cfg(nkey_seed_file="/run/secrets/w.nk"))._connect_options(None) == {
        "nkeys_seed": "/run/secrets/w.nk"
    }


def _captured_warnings(monkeypatch):
    """Collect records from adapy's own logger.

    `caplog` cannot see them: `ada` does not propagate to the root logger, so a
    warning that plainly reaches stderr never reaches the fixture. Attaching a
    handler is the honest way to assert on it.
    """
    import logging

    from ada.config import logger as ada_logger

    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Collect()
    ada_logger.addHandler(handler)
    monkeypatch.setattr(ada_logger, "level", logging.WARNING, raising=False)
    return records, lambda: ada_logger.removeHandler(handler)


def test_the_file_wins_when_both_are_set_and_says_so(monkeypatch):
    _present(monkeypatch, "nkeys")
    # Redundant rather than dangerous — both name the same principal in any
    # sane deployment — so this warns and picks one deterministically instead
    # of refusing to start. A silent coin flip would surface as an auth failure
    # that reads like a network problem.
    records, cleanup = _captured_warnings(monkeypatch)
    try:
        opts = JobQueue(_cfg(nkey_seed_file="/run/secrets/w.nk", nkey_seed="SUAWKEY"))._connect_options(None)
    finally:
        cleanup()

    assert opts == {"nkeys_seed": "/run/secrets/w.nk"}
    assert any("using the file" in r for r in records), records


def test_the_seed_never_appears_in_the_warning(monkeypatch):
    _present(monkeypatch, "nkeys")
    # It is a private key. A warning about configuration must not leak it.
    records, cleanup = _captured_warnings(monkeypatch)
    try:
        JobQueue(_cfg(nkey_seed_file="/run/secrets/w.nk", nkey_seed="SUPERSECRETSEED"))._connect_options(None)
    finally:
        cleanup()

    assert records, "expected the ambiguity warning"
    assert not any("SUPERSECRETSEED" in r for r in records)


# --- settings -------------------------------------------------------------


def test_env_populates_the_inline_seed(monkeypatch):
    monkeypatch.setenv("ADA_VIEWER_NATS_NKEY_SEED_VALUE", "  SUAWKEY\n")

    # Stripped: a seed is base32 with no surrounding whitespace, and a secret
    # store that appends a newline is common enough that not stripping would
    # turn a correct secret into an auth failure.
    assert load_settings().queue.nkey_seed == "SUAWKEY"


def test_the_two_seed_settings_stay_distinct(monkeypatch):
    monkeypatch.setenv("ADA_VIEWER_NATS_NKEY_SEED", "/run/secrets/w.nk")
    monkeypatch.setenv("ADA_VIEWER_NATS_NKEY_SEED_VALUE", "SUAWKEY")

    queue = load_settings().queue

    # The asymmetric naming is deliberate: the file form shipped first, and
    # flipping the meaning of a released env var would be a silent credential
    # change.
    assert queue.nkey_seed_file == "/run/secrets/w.nk"
    assert queue.nkey_seed == "SUAWKEY"


def test_both_seed_settings_default_to_absent(monkeypatch):
    monkeypatch.delenv("ADA_VIEWER_NATS_NKEY_SEED", raising=False)
    monkeypatch.delenv("ADA_VIEWER_NATS_NKEY_SEED_VALUE", raising=False)

    queue = load_settings().queue

    assert queue.nkey_seed_file == "" and queue.nkey_seed == ""
    assert JobQueue(queue)._connect_options(None) == {}
