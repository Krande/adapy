"""Storing a convex-contact clash run and listing it beside its target -- one implementation for every
writer: the route a browser posts its run to, the worker's ``clash_contacts`` job and the queue-less
viewer's local engine.

LIGHT ON PURPOSE: stdlib plus ``ada.clash.sidecar``, so the slim API can store a browser's run
without the modelling stack. A run goes to its sidecar key (``ada.clash.sidecar.run_key``) and a line
for it into its target's index; the index is read-modify-write and last write wins, the ``_groups/``
precedent -- two runs finishing in the same instant can drop a line from the listing, never a run.
"""

from __future__ import annotations

import json

from ada.clash.sidecar import ContactRun, index_key, index_with, parse_run

__all__ = ["index_update", "write_run_and_index", "write_run_and_index_sync"]


def index_update(run: ContactRun, run_key: str, current: bytes | None) -> tuple[str, bytes]:
    """``(index key, new index bytes)`` with ``run`` recorded at ``run_key``."""
    return index_key(run.target), json.dumps(index_with(current, run.summary(run_key))).encode("utf-8")


async def write_run_and_index(storage, scope, run_key: str, run_bytes: bytes) -> ContactRun:
    """Store a run (async storage) and record it in its target's index. Returns the parsed run."""
    run = parse_run(run_bytes)
    await storage.put_bytes(scope, run_key, run_bytes, content_encoding="gzip")
    key = index_key(run.target)
    try:
        current = await storage.get_bytes(scope, key)
    except (FileNotFoundError, KeyError):
        current = None
    key, data = index_update(run, run_key, current)
    await storage.put_bytes(scope, key, data, content_encoding="gzip")
    return run


def write_run_and_index_sync(storage, run_key: str, run_bytes: bytes) -> ContactRun:
    """The same through a synchronous storage facade (``key`` only, scope bound)."""
    run = parse_run(run_bytes)
    storage.put_bytes(run_key, run_bytes, content_encoding="gzip")
    try:
        current = storage.get_bytes(index_key(run.target))
    except (FileNotFoundError, KeyError):
        current = None
    key, data = index_update(run, run_key, current)
    storage.put_bytes(key, data, content_encoding="gzip")
    return run
