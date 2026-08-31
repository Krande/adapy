"""Compile logs belong to a RUN, not to a document.

Every procedural derived key is content-addressed on purpose — a revision stamp
for a committed compile, the document's content hash for a preview — so an
unchanged input is served from cache for free. Deriving the log key from the
artifact key inherited that property, and a log has no business inheriting it:
two different compile ATTEMPTS of the same document shared one log key, and the
worker only wrote it when the engine had actually emitted something. A forced
recompile that succeeded quietly therefore left the PREVIOUS attempt's failure
sitting at that key for the viewer to show.

These tests pin the replacement: the queue job id is the run id, each run writes
its own log unconditionally, the artifact carries a ``.run`` pointer to whichever
run last targeted it, and each run opens an ``audit_log`` row so the admin panel
can serve it through the same per-row "Log" tab a conversion gets.
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import tempfile
from types import SimpleNamespace

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import db as db_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.procedural import (  # noqa: E402
    RUN_LOG_RETENTION,
    doc_content_hash,
    is_valid_run_id,
    procedural_glb_key,
    procedural_log_key,
    procedural_preview_glb_key,
    procedural_run_dir,
    procedural_run_log_key,
    procedural_run_pointer_key,
    prune_run_log_keys,
)
from ada.comms.rest.queue import JobQueue  # noqa: E402

# The worker is a POSIX-only component (it forks conversions through fcntl), so
# the handler-level tests below skip off-POSIX and run in CI like every other
# worker test. The key-convention and endpoint tests run everywhere.
try:
    from ada.comms.rest import worker as worker_mod  # noqa: E402
except ImportError:  # pragma: no cover - non-POSIX dev machine
    worker_mod = None

needs_worker = pytest.mark.skipif(worker_mod is None, reason="ada.comms.rest.worker requires POSIX (fcntl)")


def _settings(tmp_path: pathlib.Path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(tmp_path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=None,
            stream="ada",
            subject="ada.viewer.jobs.convert",
            kv_bucket="ada-viewer-jobs",
            durable="ada-viewer-worker",
        ),
        auth=AuthConfig(
            enabled=False,
            issuer="",
            client_id="",
            audience="",
            admin_group="",
            cli_token_secret="",
        ),
        database_url="",
    )


# ── key conventions ──────────────────────────────────────────────────


def test_run_log_key_is_keyed_by_run_not_by_artifact():
    from ada.comms.rest.converter import is_hidden_key

    key = procedural_run_log_key("m1", "abc123")
    assert key == "_procedural/m1/runs/abc123.log"
    assert is_hidden_key(key)
    assert key.startswith(procedural_run_dir("m1"))
    # THE POINT: the artifact key is shared by every compile of an unchanged
    # input, the run key never is.
    assert procedural_log_key(procedural_glb_key("m1", 4)) == procedural_log_key(procedural_glb_key("m1", 4))
    assert procedural_run_log_key("m1", "run-a") != procedural_run_log_key("m1", "run-b")
    # ...including for a preview, whose key is the doc's content hash: the same
    # doc previewed twice hits one blob key but two distinct run logs.
    h = doc_content_hash({"spaces": []})
    assert procedural_preview_glb_key("m1", h) == procedural_preview_glb_key("m1", h)
    assert procedural_run_log_key("m1", "prev-1") != procedural_run_log_key("m1", "prev-2")


def test_run_id_must_be_key_safe():
    assert is_valid_run_id("0f9a3c4e5b6d7a8b")
    assert is_valid_run_id("wasm-0f9a3c4e")
    # A run id is interpolated into a blob key, so path-ish and empty ids are
    # rejected outright rather than escaped.
    for bad in ("", "../../etc/passwd", "a/b", "run.log", "x" * 200):
        assert not is_valid_run_id(bad), bad
        with pytest.raises(ValueError):
            procedural_run_log_key("m1", bad)


def test_run_pointer_key_is_artifact_sibling():
    assert procedural_run_pointer_key("_procedural/m1/r2.glb") == "_procedural/m1/r2.glb.run"
    assert procedural_run_pointer_key("_procedural/m1/preview/deadbeef.glb") == (
        "_procedural/m1/preview/deadbeef.glb.run"
    )


def test_run_log_retention_keeps_the_newest():
    keys = [f"k{i}" for i in range(RUN_LOG_RETENTION + 3)]
    doomed = prune_run_log_keys(keys)
    assert doomed == keys[RUN_LOG_RETENTION:]
    assert len(doomed) == 3
    # Under the limit nothing is dropped.
    assert prune_run_log_keys(keys[:2]) == []


# ── the worker: one log per run ──────────────────────────────────────


class _FakeStorage:
    """In-memory stand-in for Storage, recording writes/deletes by key."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.deleted: list[str] = []

    async def exists(self, scope, key):
        return key in self.blobs

    async def get_bytes(self, scope, key):
        if key not in self.blobs:
            raise FileNotFoundError(key)
        return self.blobs[key]

    async def put_bytes(self, scope, key, data, content_encoding=None):
        self.blobs[key] = data

    async def delete(self, scope, key):
        self.deleted.append(key)
        self.blobs.pop(key, None)

    async def list_prefix(self, scope, prefix):
        return [
            SimpleNamespace(key=k, size=len(v), last_modified=None)
            for k, v in self.blobs.items()
            if k.startswith(prefix)
        ]


class _FakeQueue:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def update(self, job_id, **kw):
        self.updates.append(kw)


def _compile_job(job_id: str, derived_key: str) -> SimpleNamespace:
    return SimpleNamespace(
        job_id=job_id,
        derived_key=derived_key,
        conversion_options={"model_id": "m1", "revision": 2, "lod": "sim", "engine": None},
    )


def _run_build(monkeypatch, storage, *, job_id: str, derived_key: str, compile_fn) -> _FakeQueue:
    """Drive ``_run_procedural_build`` with the DB + the engine stubbed out, so the
    test exercises the real logging/keying path around a compile of our choosing."""
    row = {
        "id": "m1",
        "revision": 2,
        "scope_kind": "shared",
        "scope_id": None,
        "engine": None,
        "name": "M",
        "doc": {"spaces": [], "equipments": []},
    }

    async def _get_model(pool, model_id):
        return row

    async def _catalog(pool, *, scope_kind, scope_id):
        return {}

    async def _cad_keys(pool, *, scope_kind, scope_id):
        return {}

    monkeypatch.setattr(db_module, "get_procedural_model", _get_model)
    monkeypatch.setattr(db_module, "get_equipment_docs_by_scope", _catalog)
    monkeypatch.setattr(db_module, "get_equipment_cad_keys_by_scope", _cad_keys)
    monkeypatch.setattr("ada.topo_model.compile.compile_procedural_doc_with_takeoff", compile_fn)

    queue = _FakeQueue()
    asyncio.run(
        worker_mod._run_procedural_build(
            job=_compile_job(job_id, derived_key),
            scope=SimpleNamespace(kind="shared", id=None),
            storage=storage,
            queue=queue,
            db_pool=object(),
            started_at=0.0,
        )
    )
    return queue


@needs_worker
def test_second_compile_of_the_same_doc_gets_its_own_log(monkeypatch):
    # THE REPORTED BUG. Run 1 fails loudly. Run 2 compiles the very same document
    # (same derived key) and succeeds SILENTLY — the engine emits nothing. Under
    # the old artifact-keyed scheme run 2 wrote no log at all and the panel kept
    # serving run 1's error; now each run owns its own blob and the artifact
    # pointer follows the latest run.
    storage = _FakeStorage()
    derived_key = procedural_glb_key("m1", 2)

    def _boom(doc, **kw):
        raise RuntimeError("engine exploded")

    def _quiet(doc, **kw):
        return b"glTF", {"mass": 1}

    _run_build(monkeypatch, storage, job_id="run1", derived_key=derived_key, compile_fn=_boom)
    _run_build(monkeypatch, storage, job_id="run2", derived_key=derived_key, compile_fn=_quiet)

    log1 = storage.blobs[procedural_run_log_key("m1", "run1")].decode()
    log2 = storage.blobs[procedural_run_log_key("m1", "run2")].decode()

    # Two runs, two logs — neither overwrote the other.
    assert log1 != log2
    # The failure is retrievable and reads as a failure...
    assert "run run1" in log1 and "failed at build" in log1 and "engine exploded" in log1
    # ...and the later success does NOT carry it. This is the exact regression:
    # a silent success used to leave the earlier error in place.
    assert "engine exploded" not in log2
    assert "run run2" in log2 and "compile ok" in log2
    # The artifact now points at the run that most recently targeted it, so a
    # key-only lookup resolves to run 2.
    assert storage.blobs[procedural_run_pointer_key(derived_key)] == b"run2"


@needs_worker
def test_failed_run_log_survives_the_success_that_follows(monkeypatch):
    # A failure's log must stay independently retrievable AFTER a later run
    # succeeds — the run id is the handle the admin audit row keeps.
    storage = _FakeStorage()
    derived_key = procedural_glb_key("m1", 2)

    def _boom(doc, **kw):
        raise RuntimeError("bad blueprint")

    def _ok(doc, **kw):
        return b"glTF", {}

    _run_build(monkeypatch, storage, job_id="failrun", derived_key=derived_key, compile_fn=_boom)
    _run_build(monkeypatch, storage, job_id="okrun", derived_key=derived_key, compile_fn=_ok)

    failed = storage.blobs[procedural_run_log_key("m1", "failrun")].decode()
    ok = storage.blobs[procedural_run_log_key("m1", "okrun")].decode()
    assert "failed at build" in failed and "bad blueprint" in failed
    assert "compile ok" in ok and "failed" not in ok
    # No GLB was written for the failed run, but its log is there regardless.
    assert storage.blobs[derived_key] == b"glTF"


@needs_worker
def test_run_that_dies_before_the_engine_still_leaves_a_log(monkeypatch):
    # A run that fails during setup (here: the model row is gone) never reaches
    # the engine. It must still write its own log rather than leaving the reader
    # with whatever the previous run wrote.
    storage = _FakeStorage()
    derived_key = procedural_glb_key("m1", 2)

    async def _no_model(pool, model_id):
        return None

    monkeypatch.setattr(db_module, "get_procedural_model", _no_model)
    asyncio.run(
        worker_mod._run_procedural_build(
            job=_compile_job("earlyfail", derived_key),
            scope=SimpleNamespace(kind="shared", id=None),
            storage=storage,
            queue=_FakeQueue(),
            db_pool=object(),
            started_at=0.0,
        )
    )
    log = storage.blobs[procedural_run_log_key("m1", "earlyfail")].decode()
    assert "failed at build" in log and "not found" in log
    # The pointer is claimed up front, so the viewer finds THIS run for the key.
    assert storage.blobs[procedural_run_pointer_key(derived_key)] == b"earlyfail"


@needs_worker
def test_run_log_reaches_the_audit_row(monkeypatch):
    # The worker hands the run's log key to the audit row, which is what lets the
    # admin panel's existing per-row Log tab serve a compile like a conversion.
    storage = _FakeStorage()
    patched: list[dict] = []

    async def _update(pool, **kw):
        patched.append(kw)

    monkeypatch.setattr(db_module, "update_audit_by_job", _update)
    _run_build(
        monkeypatch,
        storage,
        job_id="audited",
        derived_key=procedural_glb_key("m1", 2),
        compile_fn=lambda doc, **kw: (b"glTF", {}),
    )
    assert patched, "no audit row patched"
    done = patched[-1]
    assert done["job_id"] == "audited"
    assert done["status"] == "done"
    assert done["log_key"] == procedural_run_log_key("m1", "audited")


@needs_worker
def test_old_run_logs_are_pruned(monkeypatch):
    # Run-keyed logs accumulate where the artifact-keyed one overwrote itself, so
    # the prefix is bounded. Seed past the retention limit and compile once.
    storage = _FakeStorage()
    for i in range(RUN_LOG_RETENTION + 5):
        storage.blobs[procedural_run_log_key("m1", f"old{i:04d}")] = b"x"
    _run_build(
        monkeypatch,
        storage,
        job_id="fresh",
        derived_key=procedural_glb_key("m1", 2),
        compile_fn=lambda doc, **kw: (b"glTF", {}),
    )
    assert storage.deleted, "retention sweep deleted nothing"
    remaining = [k for k in storage.blobs if k.startswith(procedural_run_dir("m1"))]
    assert len(remaining) == RUN_LOG_RETENTION
    # The run that just finished is never the one pruned — its log is exactly what
    # the caller is about to be handed.
    assert procedural_run_log_key("m1", "fresh") in remaining


# ── the endpoint: serve a run, not an artifact ───────────────────────


@pytest.fixture
def log_client(monkeypatch, tmp_path: pathlib.Path):
    """App + storage wired against a stubbed model row (no Postgres needed)."""
    row = {"id": "m1", "revision": 2, "scope_kind": "shared", "scope_id": None, "engine": None, "name": "M", "doc": {}}

    async def _get_model(pool, model_id):
        return row if model_id == "m1" else None

    monkeypatch.setattr(db_module, "get_procedural_model", _get_model)
    settings = _settings(tmp_path)
    from ada.comms.rest.scope import Scope
    from ada.comms.rest.storage import Storage

    storage = Storage.from_settings(settings)

    def seed(key: str, text: bytes) -> None:
        asyncio.run(storage.put_bytes(Scope.shared(), key, text))

    app = create_app(settings)
    with TestClient(app) as client:
        client.app.state.db_pool = object()
        yield client, seed


def test_compile_log_endpoint_serves_the_requested_run(log_client):
    client, seed = log_client
    seed(procedural_run_log_key("m1", "runA"), b"=== compile ok - run runA ===")
    seed(procedural_run_log_key("m1", "runB"), b"=== compile failed at build - run runB ===")

    base = "/api/scopes/shared/procedural-models/m1/compile-log"
    r = client.get(base, params={"run": "runA"})
    assert r.status_code == 200, r.text
    assert "run runA" in r.text
    assert r.headers["X-Compile-Run"] == "runA"
    # The other run is a separate, independently addressable log.
    assert "run runB" in client.get(base, params={"run": "runB"}).text
    # A run with no log is an empty 200, not an error (the compile may still be
    # in flight) — and it is never someone else's log.
    r = client.get(base, params={"run": "runC"})
    assert r.status_code == 200 and r.text == ""


def test_compile_log_endpoint_rejects_unsafe_input(log_client):
    client, _ = log_client
    base = "/api/scopes/shared/procedural-models/m1/compile-log"
    assert client.get(base).status_code == 400  # neither run nor key
    assert client.get(base, params={"run": "../../secret"}).status_code == 400
    # The key path stays confined to this model's own prefix.
    assert client.get(base, params={"key": "_procedural/other/r1.glb"}).status_code == 400


def test_compile_log_endpoint_resolves_a_cached_artifact_through_its_pointer(log_client):
    # A result served from cache has no run of its own; the artifact's pointer
    # names the run that built it, and the response says which run that was so
    # the caller can tell it apart from the run it just triggered.
    client, seed = log_client
    key = procedural_glb_key("m1", 2)
    seed(procedural_run_pointer_key(key), b"builder-run")
    seed(procedural_run_log_key("m1", "builder-run"), b"built here")

    r = client.get("/api/scopes/shared/procedural-models/m1/compile-log", params={"key": key})
    assert r.status_code == 200
    assert r.text == "built here"
    assert r.headers["X-Compile-Run"] == "builder-run"


def test_compile_log_endpoint_falls_back_to_the_legacy_sibling(log_client):
    # An artifact compiled before runs existed has no pointer; its ``.log``
    # sibling still resolves, flagged with an empty run id so the caller knows it
    # cannot attribute the log to any run.
    client, seed = log_client
    key = procedural_glb_key("m1", 2)
    seed(procedural_log_key(key), b"pre-runs log")

    r = client.get("/api/scopes/shared/procedural-models/m1/compile-log", params={"key": key})
    assert r.status_code == 200
    assert r.text == "pre-runs log"
    assert r.headers["X-Compile-Run"] == ""


# ── the endpoint: a compile opens an audit row ───────────────────────


def test_compile_opens_an_audit_row_for_the_run(monkeypatch, tmp_path: pathlib.Path):
    # The run is visible in the admin audit log from the moment it is enqueued,
    # keyed by the same job id the worker later patches with the outcome and the
    # run's log key.
    monkeypatch.setattr(JobQueue, "enabled", property(lambda self: True))
    audits: list[dict] = []

    async def _fake_enqueue(self, source_key, target_format="glb", **kw):
        return SimpleNamespace(job_id="job-xyz")

    async def _insert_audit(pool, **kw):
        audits.append(kw)

    async def _get_model(pool, model_id):
        return {
            "id": "m1",
            "revision": 2,
            "scope_kind": "shared",
            "scope_id": None,
            "engine": None,
            "name": "M",
            "doc": {"spaces": []},
        }

    monkeypatch.setattr(JobQueue, "enqueue", _fake_enqueue)
    monkeypatch.setattr(JobQueue, "list_workers", lambda self: _noop())
    monkeypatch.setattr(JobQueue, "connect", lambda self, **kwargs: _noop())
    monkeypatch.setattr(db_module, "get_procedural_model", _get_model)
    monkeypatch.setattr(db_module, "insert_audit", _insert_audit)

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        client.app.state.db_pool = object()
        r = client.post("/api/scopes/shared/procedural-models/m1/compile")
    assert r.status_code == 200, r.text
    assert r.json()["job_id"] == "job-xyz"
    row = next(a for a in audits if a["action"] == "compile")
    # Same id the response returned: the run id joins the response, the log blob
    # and the audit row.
    assert row["job_id"] == "job-xyz"
    assert row["status"] == "queued"
    assert row["target_format"] == "procedural_build"
    assert row["key"] == procedural_glb_key("m1", 2)


async def _noop():
    return []
