"""The worker's format registry replaces the ``if job.target_format == ...`` chain.

Pins (1) that every job kind the old chain dispatched resolves to a handler of that kind,
with the same pre-download (synthetic) vs post-download (source-backed) placement; (2) that
every ConverterRegistry target — and anything unknown — falls through to the convert
handler, so an unknown format still fails inside ``convert()`` with the converter's own
``UnsupportedFormat`` message; (3) one end-to-end ``_process_one`` job per kind the rest of
the suite does not already drive.
"""

from __future__ import annotations

import pathlib

import pytest
from obstore.store import LocalStore

from ada.comms.rest import formats
from ada.comms.rest.converter import ConverterRegistry
from ada.comms.rest.formats import registry
from ada.comms.rest.queue import Job
from ada.comms.rest.scope import Scope
from ada.comms.rest.storage import Storage
from ada.comms.rest.subprocess_convert import HAVE_POSIX_FORK
from ada.comms.rest.worker import _process_one

# (kind, dispatched before any source download) — the old chain's branches, in order.
CHAIN_KINDS = [
    ("component_build", True),
    ("procedural_build", True),
    ("plugin_job", True),
    ("procedural_detail", True),
    ("procedural_relocations", True),
    ("procedural_export_xlsx", True),
    ("procedural_export_model", True),
    ("procedural_import_xlsx", True),
    ("equipment_bbox", True),
    ("procedural_engine_build", True),
    ("utility", False),
    ("fea_artefacts", False),
    ("fea_meta", False),
    ("parity", False),
]


def _job(kind: str, **kw) -> Job:
    return Job(
        job_id=kw.pop("job_id", f"job-{kind}"),
        source_key=kw.pop("source_key", "a.glb"),
        derived_key=kw.pop("derived_key", f"_derived/a.glb.{kind}"),
        status="queued",
        target_format=kind,
        **kw,
    )


class _FakeQueue:
    def __init__(self, job: Job) -> None:
        self.job = job
        self.updates: list[dict] = []

    async def get(self, job_id):
        return self.job if job_id == self.job.job_id else None

    async def update(self, job_id, **kw):
        self.updates.append(kw)

    @property
    def final(self) -> dict:
        return self.updates[-1]


# ── resolution ───────────────────────────────────────────────────────


@pytest.mark.parametrize("kind,synthetic", CHAIN_KINDS)
def test_every_chain_kind_resolves_to_its_own_handler(kind, synthetic):
    handler = registry.resolve(_job(kind))
    assert handler.kind == kind
    assert handler.needs_source is (not synthetic)
    # The one cache-exempt kind, as before: its output is a DB side-effect, not the blob.
    assert handler.skip_cached_short_circuit is (kind == "equipment_bbox")


def test_registered_kinds_are_exactly_the_chain_plus_the_convert_fallback():
    assert registry.registered_kinds() == [k for k, _ in CHAIN_KINDS] + ["convert"]
    assert formats.registered_kinds() == registry.registered_kinds()


@pytest.mark.parametrize("target", sorted(ConverterRegistry.all_targets()))
def test_every_converter_registry_target_resolves_to_the_convert_handler(target):
    handler = registry.resolve(_job(target))
    assert handler.kind == "convert"
    assert handler.needs_source is True


def test_an_unknown_format_resolves_to_the_convert_handler_too():
    # It is convert() that rejects it, with the converter's message — see the e2e test below.
    assert registry.resolve(_job("bogus")).kind == "convert"


def test_registering_the_same_kind_again_replaces_it():
    class _Alt(registry.SyntheticFormatHandler):
        kind = "component_build"

        async def run(self, job, ctx):
            pass

    before = registry.handlers()
    alt = _Alt()
    try:
        registry.register(alt)
        assert registry.resolve(_job("component_build")) is alt
        assert len(registry.handlers()) == len(before)
    finally:
        registry.register(before[0])
        assert registry.resolve(_job("component_build")) is before[0]


def test_resolve_without_a_fallback_raises(monkeypatch):
    monkeypatch.setattr(registry, "_FALLBACK", None)
    with pytest.raises(registry.UnknownJobKind):
        registry.resolve(_job("bogus"))


# ── end to end through _process_one ───────────────────────────────────


@pytest.fixture
def storage(tmp_path: pathlib.Path) -> Storage:
    return Storage(LocalStore(str(tmp_path)), prefix="")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,error",
    [
        ("component_build", "conversion_options.spec_name is required for component_build"),
        ("procedural_build", "conversion_options.model_id and revision are required for procedural_build"),
        ("plugin_job", "conversion_options.plugin_id is required for a plugin_job"),
        (
            "procedural_detail",
            "conversion_options.detailing_entrypoint (module:callable) is required for procedural_detail",
        ),
        ("procedural_relocations", "conversion_options.model_id and revision are required for procedural_relocations"),
        ("procedural_export_xlsx", "conversion_options.model_id and revision are required for procedural_export_xlsx"),
        (
            "procedural_export_model",
            "conversion_options.model_id and revision are required for procedural_export_model",
        ),
        ("procedural_import_xlsx", "conversion_options.source_key is required for procedural_import_xlsx"),
        ("equipment_bbox", "conversion_options.type_id and cad_key are required for equipment_bbox"),
        ("procedural_engine_build", "conversion_options.engine_id is required for procedural_engine_build"),
    ],
)
async def test_synthetic_kinds_run_before_any_source_download(monkeypatch, storage, kind, error):
    """A synthetic job has no source: the handler runs first and reports its own outcome.
    Here each one refuses (empty options) with its own message — which is also proof the
    shared pre-steps never went looking for ``a.glb`` (absent from storage; that would have
    failed the job as ``loading`` / missing instead)."""

    async def _no_fetch(*a, **kw):
        raise AssertionError("a synthetic job must not download a source")

    monkeypatch.setattr("ada.comms.rest.worker.process.fetch_source", _no_fetch)
    job = _job(kind, conversion_options={})
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.updates[0] == {"status": "running", "stage": "loading", "progress": 0.05}
    assert queue.final["status"] == "error"
    assert error in queue.final["error"]


@pytest.mark.asyncio
async def test_equipment_bbox_bypasses_the_cached_blob_short_circuit(storage):
    job = _job("equipment_bbox", conversion_options={}, derived_key="_derived/preview.glb")
    await storage.put_bytes(Scope.shared(), job.derived_key, b"stale preview")
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    # Not "cached": the handler ran (and refused for want of a DB).
    assert [u.get("stage") for u in queue.updates][0] == "loading"
    assert queue.final["error"] == "conversion_options.type_id and cad_key are required for equipment_bbox"


@pytest.mark.asyncio
async def test_other_kinds_short_circuit_on_a_cached_blob(storage):
    job = _job("procedural_build", conversion_options={})
    await storage.put_bytes(Scope.shared(), job.derived_key, b"built")
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.updates == [{"status": "done", "stage": "cached", "progress": 1.0, "error": None}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,module,fn",
    [
        ("fea_artefacts", "fea", "_run_fea_artefact_bake"),
        ("fea_meta", "fea", "_run_fea_meta_compute"),
        ("parity", "parity", "_run_parity_validation"),
        ("utility", "utility", "_run_utility_job"),
    ],
)
async def test_source_backed_kinds_get_the_downloaded_source(monkeypatch, storage, kind, module, fn):
    """The shared pre-steps stream the source to a worker-local tempfile, hand the handler its
    path + progress callback, and unlink it afterwards."""
    scope = Scope.shared()
    await storage.put_bytes(scope, "a.glb", b"glb-bytes")
    seen: dict = {}

    async def _record(**kw):
        seen.update(kw)
        seen["bytes_on_disk"] = kw["src_path"].read_bytes()
        await kw["_on_progress"]("probing", 0.5)

    monkeypatch.setattr(f"ada.comms.rest.formats.{module}.{fn}", _record)
    job = _job(kind, conversion_options={})
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert seen["job"] is job
    assert seen["bytes_on_disk"] == b"glb-bytes"
    assert seen["src_path"].suffix == ".glb" and not seen["src_path"].exists()
    assert seen["started_at"] > 0 and seen["db_pool"] is None and seen["storage"] is storage
    if kind == "parity":
        assert "timeout_s" in seen and seen["timeout_s"] is None
    assert {"stage": "probing", "progress": 0.5} in queue.updates


@pytest.mark.asyncio
async def test_utility_end_to_end_reports_its_own_error(storage):
    await storage.put_bytes(Scope.shared(), "a.glb", b"glb-bytes")
    job = _job("utility", conversion_options={})
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.final["status"] == "error"
    assert queue.final["error"] == "conversion_options.utility_name is required for a utility job"


@pytest.mark.asyncio
async def test_a_missing_source_fails_the_job_at_loading(storage):
    job = _job("glb")
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.final["status"] == "error" and queue.final["stage"] == "loading"


needs_fork = pytest.mark.skipif(not HAVE_POSIX_FORK, reason="the convert handler forks the conversion child")


@needs_fork
@pytest.mark.asyncio
async def test_an_unknown_format_fails_with_the_converter_error(storage):
    """Same error as the chain produced: the job reaches convert() in the child, which rejects
    the target with the registry's ``UnsupportedFormat`` message."""
    await storage.put_bytes(Scope.shared(), "a.glb", b"glb-bytes")
    job = _job("bogus", conversion_options={"glb_compression": "off"})
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.final["status"] == "error" and queue.final["stage"] == "convert"
    assert queue.final["error"].startswith(
        "UnsupportedFormat: no converter registered for '.glb' -> 'bogus'; viable targets: "
    )


@needs_fork
@pytest.mark.asyncio
async def test_convert_end_to_end_uploads_the_derived_blob(storage):
    scope = Scope.shared()
    await storage.put_bytes(scope, "a.glb", b"glb-bytes")
    job = _job("glb", derived_key="_derived/a.glb", conversion_options={"glb_compression": "off"})
    queue = _FakeQueue(job)
    await _process_one(job.job_id, queue, storage, None, None)
    assert queue.final == {"status": "done", "stage": "ready", "progress": 1.0, "error": None}
    assert {"stage": "uploading", "progress": 0.95} in queue.updates
    assert await storage.exists(scope, "_derived/a.glb")


def test_job_context_defaults_are_the_synthetic_view():
    ctx = registry.JobContext(scope=Scope.shared(), storage=None, queue=None, db_pool=None, started_at=1.0)
    assert (ctx.src_path, ctx.on_progress, ctx.settings, ctx.fetch) == (None, None, None, None)
