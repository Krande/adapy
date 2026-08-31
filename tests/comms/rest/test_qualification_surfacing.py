"""A withheld capability must reach the caller as unavailable, not as absent.

The gate itself is tested in test_qualification.py. This is the half that makes
it usable: `/api/plugins` lists only what live workers advertise, so a worker
that withholds a capability would otherwise vanish from it and look exactly like
a machine that is switched off. With one member in the pool that is worse than
no gate — the caller is told to start a worker that is already running.

See deploy/worker-trust.md §4, "Withheld is not absent".
"""

from __future__ import annotations

import os
import tempfile
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-qual-storage-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.queue import JobQueue  # noqa: E402

PLUGIN = "cad-export"


def _settings(tmp_path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(url="nats://test-not-dialled", stream="ada", subject="s", kv_bucket="kv", durable="d"),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


def _spec(**over):
    base = {"slug": PLUGIN, "id": PLUGIN, "name": "CAD export", "worker_capability": "cad"}
    base.update(over)
    return base


def _worker(worker_id, *, withheld=None, capabilities=("cad",), spec=None):
    return {
        "worker_id": worker_id,
        "last_heartbeat": time.time(),
        "capabilities": list(capabilities),
        "withheld": withheld or [],
        "plugin_specs": [spec or _spec()],
    }


def _plugins(monkeypatch, tmp_path, workers):
    async def _connect(self, **kw):
        return None

    async def _list(self):
        return workers

    monkeypatch.setattr(JobQueue, "connect", _connect)
    monkeypatch.setattr(JobQueue, "list_workers", _list)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        return client.get("/api/plugins").json()["plugins"]


def _entry(plugins):
    return next((p for p in plugins if p["slug"] == PLUGIN), None)


# --- the correction ---------------------------------------------------------


def test_a_withheld_capability_is_reported_unavailable_with_its_reason(monkeypatch, tmp_path):
    reason = "ada-py 0.44.1 does not satisfy >=0.51.0"
    plugins = _plugins(monkeypatch, tmp_path, [_worker("w1", withheld=[{"capability": "cad", "reason": reason}])])

    e = _entry(plugins)
    assert e is not None, "a withheld capability must not vanish from the listing"
    assert e["available"] is False
    assert e["unavailable_reason"] == reason


def test_a_fit_worker_carries_no_availability_flag(monkeypatch, tmp_path):
    # Absent rather than `available: true`, so a client written before this
    # existed reads a fit plugin exactly as it always did.
    e = _entry(_plugins(monkeypatch, tmp_path, [_worker("w1")]))
    assert "available" not in e and "unavailable_reason" not in e


def test_one_fit_worker_makes_the_capability_available_again(monkeypatch, tmp_path):
    """What the caller needs to know is whether ANYONE can serve it."""
    plugins = _plugins(
        monkeypatch,
        tmp_path,
        [
            _worker("w1", withheld=[{"capability": "cad", "reason": "stale"}]),
            _worker("w2"),
        ],
    )
    e = _entry(plugins)
    assert e.get("available") is not False
    assert "unavailable_reason" not in e


def test_worker_order_does_not_decide_availability(monkeypatch, tmp_path):
    """The unfit worker sorting first must not leave the plugin unavailable.

    `_live_worker_specs` visits workers in worker-id order, so without an
    explicit override the answer would depend on which name sorted first.
    """
    unfit = _worker("w1", withheld=[{"capability": "cad", "reason": "stale"}])
    fit = _worker("w2")
    forwards = _entry(_plugins(monkeypatch, tmp_path, [unfit, fit]))
    backwards = _entry(_plugins(monkeypatch, tmp_path, [fit, unfit]))

    assert forwards.get("available") == backwards.get("available")
    assert forwards.get("available") is not False


def test_a_withheld_capability_unrelated_to_the_plugin_changes_nothing(monkeypatch, tmp_path):
    # The worker withholds `meshing`; this plugin's capability is `cad`.
    e = _entry(_plugins(monkeypatch, tmp_path, [_worker("w1", withheld=[{"capability": "meshing", "reason": "x"}])]))
    assert e.get("available") is not False


def test_capability_matching_ignores_case(monkeypatch, tmp_path):
    e = _entry(_plugins(monkeypatch, tmp_path, [_worker("w1", withheld=[{"capability": "CAD", "reason": "stale"}])]))
    assert e["available"] is False


@pytest.mark.parametrize("withheld", [None, "nonsense", [1, 2], [{}], [{"reason": "no capability"}]])
def test_a_malformed_withheld_field_is_ignored_rather_than_fatal(monkeypatch, tmp_path, withheld):
    # It arrives from a worker over the wire. Garbage there must degrade to
    # "no verdict", never break the listing for every other plugin.
    w = _worker("w1")
    w["withheld"] = withheld
    e = _entry(_plugins(monkeypatch, tmp_path, [w]))
    assert e is not None
    assert e.get("available") is not False


def test_a_worker_that_predates_the_field_is_treated_as_fit(monkeypatch, tmp_path):
    # Rolling upgrade: old workers send no `withheld` at all. They must keep
    # advertising exactly as before.
    w = _worker("w1")
    del w["withheld"]
    e = _entry(_plugins(monkeypatch, tmp_path, [w]))
    assert e is not None and e.get("available") is not False


def test_a_stale_worker_is_still_excluded_entirely(monkeypatch, tmp_path):
    # Withholding is about fitness; staleness is about being alive. A dead
    # worker's verdict should not linger in the listing.
    w = _worker("w1", withheld=[{"capability": "cad", "reason": "stale"}])
    w["last_heartbeat"] = time.time() - 10_000
    assert _entry(_plugins(monkeypatch, tmp_path, [w])) is None
