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
import json
import os
import pathlib
import threading
import time
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
    # The bake runs in a forked child, so what it was called with comes back through a file.
    kwargs_file = tmp_path / "bake_kwargs.json"

    def _fake_bake(src_path, bake_dir, *, src_key, on_artefact, **kw):
        # Mesh first, a preliminary manifest + stamp, the field, then the final pair --
        # each shipped (on_artefact) the moment it is written, as bake_artefacts does.
        kwargs_file.write_text(json.dumps({k: kw[k] for k in ("partial_manifests", "stamp_extra")}))
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
    seen_kwargs = json.loads(kwargs_file.read_text())
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


def test_the_bake_runs_in_a_child_process_and_its_artefacts_are_relayed(tmp_path, monkeypatch):
    """The bake runs in a forked child: a reader holding the GIL for minutes (aba-cpp's ODB export)
    starved the worker's event loop on the executor thread, the job lease lapsed and the job was
    redelivered mid-bake. What the child writes is relayed to this process and uploaded here."""
    import ada.fem.results.artefacts as artefacts

    parent = os.getpid()

    def _fake_bake(src_path, bake_dir, *, src_key, on_artefact, **kw):
        out = pathlib.Path(bake_dir)
        (out / "fea.pid.json").write_text(str(os.getpid()))
        on_artefact(out / "fea.pid.json")
        (out / "fea.manifest.json").write_bytes(b"{}")
        on_artefact(out / "fea.manifest.json")
        return SimpleNamespace(out_dir=out)

    monkeypatch.setattr(artefacts, "bake_fea_artefacts_from_source", _fake_bake)
    monkeypatch.setattr(fea, "_audit_done", _noop_audit)
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    storage = Storage(LocalStore(str(store_dir)), prefix="")
    queue = _Queue()
    src = tmp_path / "deck.odb"
    src.write_bytes(b"odb")

    async def _progress(_stage, _frac):
        return None

    job = Job(job_id="j", source_key="d.odb", derived_key="_derived/d.odb.fea/fea.manifest.json", status="queued")
    asyncio.run(
        fea._run_fea_artefact_bake(
            job=job,
            src_path=src,
            scope=Scope.shared(),
            storage=storage,
            queue=queue,
            db_pool=None,
            started_at=0.0,
            _on_progress=_progress,
        )
    )
    assert queue.updates[-1]["status"] == "done", queue.updates
    pid = asyncio.run(storage.get_bytes(Scope.shared(), "_derived/d.odb.fea/fea.pid.json"))
    assert int(pid) != parent


def test_a_sin_still_downloading_is_read_through_the_relay(tmp_path, monkeypatch):
    """A SIN baked while it downloads: the child maps the same file and asks this process for each
    range it reads. With the background copy held back, every byte the bake reads arrives as an
    on-demand fetch made here, and the bake completes."""
    from ada.fem.formats.sesam.results.byte_source import ProgressiveDownload

    monkeypatch.setattr(fea, "_audit_done", _noop_audit)
    deck = pathlib.Path(__file__).resolve().parents[3] / (
        "files/fem_files/cantilever/sesam/static/shell/STATIC_SHELL_CANTILEVER_SESAMR1.SIN"
    )
    data = deck.read_bytes()

    class _Remote:
        """A remote whose sequential copy crawls (on-demand reads do not): the bake, decoding
        ahead of it, has to ask for its ranges."""

        def __init__(self):
            self.on_demand = 0

        def size(self):
            return len(data)

        def fetch(self, offset, length):
            if threading.current_thread().name == "progressive-download":
                time.sleep(0.2)
            else:
                self.on_demand += 1
            return data[offset : offset + length]

        def close(self):
            pass

    remote = _Remote()
    dl = ProgressiveDownload(remote, tmp_path / "source.sin", chunk_bytes=64 << 10)
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    storage = Storage(LocalStore(str(store_dir)), prefix="")
    queue = _Queue()

    async def _progress(_stage, _frac):
        return None

    try:
        asyncio.run(
            fea._run_fea_artefact_bake(
                job=Job(job_id="j", source_key="d.SIN", derived_key="x", status="queued"),
                src_path=pathlib.Path(dl.path),
                scope=Scope.shared(),
                storage=storage,
                queue=queue,
                db_pool=None,
                started_at=0.0,
                _on_progress=_progress,
                progressive=dl,
            )
        )
    finally:
        dl.close()
    assert queue.updates[-1]["status"] == "done", queue.updates
    assert remote.on_demand > 0
    keys = {e.key.rsplit("/", 1)[-1] for e in asyncio.run(storage.list(Scope.shared()))}
    assert {"fea.manifest.json", "fea.mesh.glb"} <= keys


def _bake_with_fake(tmp_path, monkeypatch, fake):
    import ada.fem.results.artefacts as artefacts

    monkeypatch.setattr(artefacts, "bake_fea_artefacts_from_source", fake)
    monkeypatch.setattr(fea, "_audit_done", _noop_audit)
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    queue = _Queue()

    async def _progress(_stage, _frac):
        return None

    src = tmp_path / "deck.sin"
    src.write_bytes(b"sin")
    asyncio.run(
        fea._run_fea_artefact_bake(
            job=Job(job_id="j", source_key="d.sin", derived_key="x", status="queued"),
            src_path=src,
            scope=Scope.shared(),
            storage=Storage(LocalStore(str(store_dir)), prefix=""),
            queue=queue,
            db_pool=None,
            started_at=0.0,
            _on_progress=_progress,
        )
    )
    return queue.updates[-1]


def _hungry_bake(src_path, bake_dir, *, src_key, on_artefact, **kw):
    """Holds ~200 MB for long enough for the watchdog to sample it, then writes a manifest."""
    hold = bytearray(200 << 20)
    hold[:: 4096] = b"x" * len(hold[:: 4096])
    time.sleep(3)
    out = pathlib.Path(bake_dir)
    (out / "fea.manifest.json").write_bytes(b"{}")
    on_artefact(out / "fea.manifest.json")
    return SimpleNamespace(out_dir=out)


def test_the_bake_is_not_held_to_the_conversion_memory_ceiling(tmp_path, monkeypatch):
    """A large deck's bake peaks past the conversion ceiling (a 2.7 GB SIN: about 5 GB against
    ~3 GB): the bake has its own, ADA_FEA_BAKE_MEM_LIMIT_MB, else the pod's limit."""
    monkeypatch.setenv("ADA_CONVERT_MEM_LIMIT_MB", "50")
    monkeypatch.setenv("ADA_FEA_BAKE_MEM_LIMIT_MB", "4000")
    assert _bake_with_fake(tmp_path, monkeypatch, _hungry_bake)["status"] == "done"


def test_a_bake_over_its_own_memory_ceiling_fails_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA_FEA_BAKE_MEM_LIMIT_MB", "50")
    last = _bake_with_fake(tmp_path, monkeypatch, _hungry_bake)
    assert last["status"] == "error" and "memory" in last["error"].lower(), last
