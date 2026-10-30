"""The FEA artefact bake uploads each file the moment the bake emits it.

A large deck's bake writes GBs across its blobs; the uploader must hand each blob to
``Storage.put_path`` rather than read it whole, and keep the per-file encoding policy: the
JSON (manifest, stamp, sidecars) gzip-at-rest, every ``.bin`` blob identity (so the viewer can
Range-read one step). The JSON is snapshotted when emitted -- the bake re-writes its preliminary
manifest as fields land -- and goes up from memory. Order is the bake's, and a failed upload
fails the job.
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
    prelim = b'{"preliminary": true, "fields": []}'
    mesh = b"glTF" + os.urandom(1024)
    seen_kwargs: dict = {}

    def _fake_bake(src_path, bake_dir, *, src_key, on_artefact, **kw):
        # Mesh first, a preliminary manifest + stamp, the field, then the final pair --
        # each shipped (on_artefact) the moment it is written, as bake_artefacts does.
        seen_kwargs.update(kw)
        out = pathlib.Path(bake_dir) / "out"
        out.mkdir()

        def write(name, data):
            (out / name).write_bytes(data)
            on_artefact(out / name)

        write("fea.mesh.glb", mesh)
        write("fea.manifest.json", prelim)
        write("fea.stamp.json", b'{"bake_version": 6, "preliminary": true, "n_fields_ready": 0, "n_fields_total": 1}')
        write("fea.u.bin", blob)
        write("fea.manifest.json", manifest)
        write("fea.stamp.json", b'{"bake_version": 6, "n_fields": 1, "job_id": "job-fea"}')
        return SimpleNamespace(out_dir=out)

    monkeypatch.setattr(artefacts, "bake_fea_artefacts_from_source", _fake_bake)
    monkeypatch.setattr(fea, "_audit_done", _noop_audit)

    uploads: list[tuple[str, str]] = []

    class _Storage(Storage):
        async def put_bytes(self, scope, key, data, **kw):
            # Only the small JSON goes from memory (snapshotted when emitted).
            assert key.endswith(".json"), f"{key} must be streamed from disk"
            uploads.append(("bytes", key))
            return await super().put_bytes(scope, key, data, **kw)

        async def put_path(self, scope, key, src_path, **kw):
            assert not key.endswith(".json")
            uploads.append(("path", key))
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
    assert seen_kwargs["partial_manifests"] is True and seen_kwargs["stamp_extra"] == {"job_id": "job-fea"}
    # In emission order, each file once per emission.
    assert [k[len(prefix) :] for _, k in uploads] == [
        "fea.mesh.glb",
        "fea.manifest.json",
        "fea.stamp.json",
        "fea.u.bin",
        "fea.manifest.json",
        "fea.stamp.json",
    ]
    # The poller learned as storage caught up: mesh first, then complete.
    ready = [u["artefacts_ready"] for u in queue.updates if "artefacts_ready" in u]
    assert ready[0] == {"mesh_ready": True, "fields_ready": 0, "fields_total": 1, "complete": False}
    assert ready[-1] == {"mesh_ready": True, "fields_ready": 1, "fields_total": 1, "complete": True}
    assert sorted(e.key for e in asyncio.run(storage.list(scope))) == sorted(
        prefix + n for n in ("fea.mesh.glb", "fea.manifest.json", "fea.stamp.json", "fea.u.bin")
    )

    assert asyncio.run(storage.get_range(scope, prefix + "fea.u.bin", 0, len(blob))) == blob  # identity
    assert asyncio.run(storage.get_range(scope, prefix + "fea.manifest.json", 0, 2)) == b"\x1f\x8b"  # gzip
    assert asyncio.run(storage.get_bytes(scope, prefix + "fea.manifest.json")) == manifest  # the final one
    assert asyncio.run(storage.get_bytes(scope, prefix + "fea.mesh.glb")) == mesh


def test_a_failed_upload_fails_the_bake(tmp_path, monkeypatch):
    import ada.fem.results.artefacts as artefacts

    def _fake_bake(src_path, bake_dir, *, src_key, on_artefact, **kw):
        out = pathlib.Path(bake_dir) / "out"
        out.mkdir()
        for name in ("fea.mesh.glb", "fea.u.bin", "fea.manifest.json"):
            (out / name).write_bytes(b"x")
            on_artefact(out / name)
        return SimpleNamespace(out_dir=out)

    monkeypatch.setattr(artefacts, "bake_fea_artefacts_from_source", _fake_bake)
    monkeypatch.setattr(fea, "_audit_done", _noop_audit)

    class _Storage(Storage):
        async def put_path(self, scope, key, src_path, **kw):
            if key.endswith("fea.u.bin"):
                raise OSError("bucket full")
            return await super().put_path(scope, key, src_path, **kw)

    store_dir = tmp_path / "store"
    store_dir.mkdir()
    storage = _Storage(LocalStore(str(store_dir)), prefix="")
    queue = _Queue()

    async def _progress(_stage, _frac):
        return None

    job = Job(job_id="j", source_key="d.sin", derived_key="_derived/d.sin.fea/fea.manifest.json", status="queued")
    asyncio.run(
        fea._run_fea_artefact_bake(
            job=job,
            src_path=tmp_path / "d.sin",
            scope=Scope.shared(),
            storage=storage,
            queue=queue,
            db_pool=None,
            started_at=0.0,
            _on_progress=_progress,
        )
    )
    assert queue.updates[-1]["status"] == "error" and "bucket full" in queue.updates[-1]["error"]
    # Nothing behind the failed blob reached storage -- no manifest naming a missing blob.
    keys = [e.key for e in asyncio.run(storage.list(Scope.shared()))]
    assert not any(k.endswith("fea.manifest.json") for k in keys)
