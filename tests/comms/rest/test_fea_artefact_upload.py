"""The FEA artefact bake uploads what it wrote by streaming each file from disk.

A large deck's bake writes GBs across its blobs; the upload step must hand each one to
``Storage.put_path`` rather than read it whole, and keep the per-file encoding policy: the
manifest JSON gzip-at-rest, every ``.bin`` blob identity (so the viewer can Range-read one step).
"""

from __future__ import annotations

import asyncio
import os
import pathlib
from types import SimpleNamespace

from obstore.store import LocalStore

from ada.comms.rest.formats import fea
from ada.comms.rest.queue import Job
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage


class _Queue:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def update(self, job_id, **kw):
        self.updates.append(kw)


async def _noop_audit(*_a, **_k):
    return None


def test_bake_artefacts_are_streamed_with_the_encoding_policy(tmp_path, monkeypatch):
    import ada.fem.results.artefacts as artefacts

    blob = os.urandom(6 << 20)  # past one upload part
    manifest = b'{"fields": ["u"], "steps": 2}'
    mesh = b"glTF" + os.urandom(1024)

    def _fake_bake(src_path, bake_dir, *, src_key):
        out = pathlib.Path(bake_dir) / "out"
        out.mkdir()
        (out / "fea.u.bin").write_bytes(blob)
        (out / "fea.manifest.json").write_bytes(manifest)
        (out / "fea.mesh.glb").write_bytes(mesh)
        return SimpleNamespace(out_dir=out)

    monkeypatch.setattr(artefacts, "bake_fea_artefacts_from_source", _fake_bake)
    monkeypatch.setattr(fea, "_audit_done", _noop_audit)

    streamed: list[str] = []

    class _Storage(Storage):
        async def put_bytes(self, *a, **kw):  # the bake must never fall back to whole-file bytes
            raise AssertionError("FEA artefacts must be uploaded with put_path")

        async def put_path(self, scope, key, src_path, **kw):
            streamed.append(key)
            return await super().put_path(scope, key, src_path, **kw)

    store_dir = tmp_path / "store"
    store_dir.mkdir()
    storage = _Storage(LocalStore(str(store_dir)), prefix="")
    scope = Scope.shared()
    src = tmp_path / "deck.SIN"
    src.write_bytes(b"deck")
    job = Job(
        job_id="job-fea",
        source_key="decks/deck.SIN",
        derived_key="_derived/decks/deck.SIN.fea/fea.manifest.json",
        status="queued",
        target_format="fea_artefacts",
    )
    queue = _Queue()

    async def _progress(_stage, _frac):
        return None

    asyncio.run(
        fea._run_fea_artefact_bake(
            job=job,
            src_path=src,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=None,
            started_at=0.0,
            _on_progress=_progress,
        )
    )

    prefix = "_derived/decks/deck.SIN.fea/"
    assert queue.updates[-1]["status"] == "done", queue.updates
    # Exactly the bake's three files -- the gzip staging copy of the manifest is not an artefact.
    assert sorted(streamed) == sorted(prefix + n for n in ("fea.u.bin", "fea.manifest.json", "fea.mesh.glb"))
    assert sorted(e.key for e in asyncio.run(storage.list(scope))) == sorted(streamed)

    assert asyncio.run(storage.get_range(scope, prefix + "fea.u.bin", 0, len(blob))) == blob  # identity
    assert asyncio.run(storage.get_range(scope, prefix + "fea.manifest.json", 0, 2)) == b"\x1f\x8b"  # gzip
    assert asyncio.run(storage.get_bytes(scope, prefix + "fea.manifest.json")) == manifest
    assert asyncio.run(storage.get_bytes(scope, prefix + "fea.mesh.glb")) == mesh
