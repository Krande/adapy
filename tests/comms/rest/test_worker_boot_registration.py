"""``build_registration`` runs end to end against a stub queue.

The worker calls it once at boot, after plugin discovery, and nothing else in the suite did, so
a lazy import inside it could break every worker in a deployment without a test noticing. This
boots it with a queue that records the three calls it makes and asserts the registration carries
the utilities it advertises.
"""

from __future__ import annotations

import asyncio

from ada.comms.rest.worker.registration import Registration, build_registration


class _StubQueue:
    def __init__(self) -> None:
        self.meta: dict[str, str] = {}
        self.registered: list[tuple[str, dict]] = []

    async def set_meta(self, key: str, value: str) -> None:
        self.meta[key] = value

    async def get_meta(self, key: str):
        return self.meta.get(key)

    async def register_worker(self, worker_id: str, payload: dict) -> None:
        self.registered.append((worker_id, payload))


def test_build_registration_boots_against_a_stub_queue(monkeypatch):
    monkeypatch.setenv("ADA_IMAGE_TAG", "test-image")
    queue = _StubQueue()

    async def boot():
        reg = await build_registration(queue)
        published = await reg.publish()
        return reg, published

    reg, published = asyncio.run(boot())
    assert isinstance(reg, Registration)
    assert published is True
    assert queue.meta.get("worker_image_tag") == "test-image"
    assert reg.worker_id
    assert isinstance(reg.source_ext_set, set)
    # The utilities registry is reached through a lazy import inside build_registration and
    # advertised in the registration heartbeat; a payload carrying the key proves the import
    # resolved and the registry was read.
    worker_id, payload = queue.registered[-1]
    assert worker_id == reg.worker_id
    assert "utilities" in payload
    assert isinstance(payload["utilities"], list)
