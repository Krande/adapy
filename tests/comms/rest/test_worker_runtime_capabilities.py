"""A plugin can grow its worker's pools, and its advertisement, without a restart.

A provider whose pools follow something it discovers -- the datasets a machine can reach -- finds a
new one after boot. Two halves have to follow it, and each fails silently alone:

* the ADVERTISEMENT: the heartbeat used to publish the plugin specs read once at boot, so a
  re-registered spec stayed invisible until a restart;
* the SUBSCRIPTION: a pool advertised and not consumed accepts jobs that then queue for ever.

These cover the pure arithmetic and the registration half against a stub queue; the loop that
subscribes needs a live JetStream consumer.
"""

from __future__ import annotations

import asyncio

import pytest

from ada import plugins
from ada.comms.rest import worker
from ada.comms.rest.plugin_registry import locally_requested_capabilities
from ada.comms.rest.worker.registration import build_registration


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


class _StubQueue:
    def __init__(self) -> None:
        self.meta: dict[str, str] = {}
        self.registered: list[dict] = []

    async def set_meta(self, key: str, value: str) -> None:
        self.meta[key] = value

    async def get_meta(self, key: str):
        return self.meta.get(key)

    async def register_worker(self, worker_id: str, payload: dict) -> None:
        self.registered.append(payload)


def test_requested_capabilities_are_kept_in_order_once_each():
    plugins.request_worker_capabilities(["pool-b", " pool-a ", "", "pool-b"])
    plugins.request_worker_capabilities(["pool-c"])
    assert plugins.requested_worker_capabilities() == ["pool-b", "pool-a", "pool-c"]
    assert locally_requested_capabilities() == ["pool-b", "pool-a", "pool-c"]


def test_only_new_tokens_are_added():
    # Compared by subject token, so a differently-cased request cannot open a second consumer.
    assert worker._capabilities_to_add(["Pool-A", "pool-b", "pool-b"], ["pool-a"]) == ["pool-b"]
    assert worker._capabilities_to_add([], ["pool-a"]) == []


def test_a_disabled_capability_cannot_be_reopened_by_a_plugin(monkeypatch):
    # The incident switch applies to a requested pool exactly as to a declared one.
    monkeypatch.setenv("ADA_WORKER_DISABLED_CAPABILITIES", "Pool-X")
    assert worker._capabilities_to_add(["pool-x", "pool-y"], []) == ["pool-y"]


def test_the_heartbeat_advertises_a_spec_registered_after_boot(monkeypatch):
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base")
    plugins.register_plugin_backend("probe", worker_capability="probe", items=["A"])
    queue = _StubQueue()

    async def run():
        reg = await build_registration(queue)
        await reg.publish()
        plugins.register_plugin_backend("probe", worker_capability="probe", items=["A", "B"])
        await reg.publish()
        return reg

    asyncio.run(run())
    first, second = queue.registered[0], queue.registered[-1]
    items = lambda payload: next(s for s in payload["plugin_specs"] if s["id"] == "probe")["items"]  # noqa: E731
    assert items(first) == ["A"]
    assert items(second) == ["A", "B"]


def test_added_capabilities_are_advertised(monkeypatch):
    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base")
    queue = _StubQueue()

    async def run():
        reg = await build_registration(queue)
        kept = reg.add_capabilities(["pool-new"])
        await reg.publish()
        return reg, kept

    reg, kept = asyncio.run(run())
    assert kept == ["pool-new"]
    assert "pool-new" in reg.capabilities
    assert "pool-new" in queue.registered[-1]["capabilities"]


def test_a_requested_capability_is_held_to_the_declared_requirements(monkeypatch):
    # The same qualification the boot capabilities pass: a requirement this worker cannot show is
    # withheld, with its reason on the registration, rather than served.
    import json

    from ada.comms.rest.qualification import CAPABILITY_REQUIREMENTS_KEY

    monkeypatch.setenv("ADA_WORKER_CAPABILITIES", "base")
    queue = _StubQueue()
    queue.meta[CAPABILITY_REQUIREMENTS_KEY] = json.dumps({"pool-gated": {"requires": {"a-package-nobody-has": ">=1"}}})

    async def run():
        reg = await build_registration(queue)
        kept = reg.add_capabilities(["pool-gated"])
        await reg.publish()
        return reg, kept

    reg, kept = asyncio.run(run())
    assert kept == []
    assert "pool-gated" not in reg.capabilities
    assert any(w["capability"] == "pool-gated" for w in queue.registered[-1]["withheld"])
