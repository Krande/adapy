"""Detailing-engine advertising + the compile ``detailing`` key routing.

Like the blueprints / design-ruleset dropdowns the detailing engines are
built-in + worker-advertised (no DB table), so the endpoint serves the static
built-ins (``none`` + ``adapy-default``) without Postgres or a live worker. The
compile ``detailing`` param composes into the derived blob key such that the
default (``none``) is BYTE-IDENTICAL to today's plain structural key.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.procedural import (  # noqa: E402
    procedural_detail_glb_key,
    procedural_detailing_glb_key,
    procedural_glb_key,
    procedural_log_key,
    procedural_preview_glb_key,
    procedural_structural_ifc_key,
)


async def _async_none():
    return None


async def _async_val(v):
    return v


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


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


# ── endpoint: built-in detailing engines served without a DB/worker ──


# A generic EXTERNAL (out-of-process) detailing engine, as a capability worker
# would advertise it in its heartbeat (adapy hardcodes no external engines — they
# are discovered only from live workers). Used to exercise the discovery + routing.
_EXT_ENGINE = {
    "slug": "ext-detail",
    "name": "External detailing",
    "description": "An external detailing engine on its own capability pool.",
    "inprocess": False,
    "entrypoint": "ext_detailing.adapy:detail",
    "worker_capability": "ext-detail",
    "joint_types": [
        {"slug": "box_to_box", "name": "Box-to-box fillet weld"},
        {"slug": "box_to_plate", "name": "Box-to-plate fillet weld"},
    ],
}


def _live_worker_advertising_ext_detail():
    """A fresh (non-stale) worker heartbeat that advertises ``_EXT_ENGINE`` — the
    shape ``JobQueue.list_workers`` returns and ``_live_worker_specs`` reads."""
    import time as _time

    return [{"last_heartbeat": _time.time(), "procedural_detailing_engine_specs": [dict(_EXT_ENGINE)]}]


def test_detailing_engines_builtin_without_db(app_client: TestClient):
    r = app_client.get("/api/scopes/shared/procedural-models/detailing-engines")
    assert r.status_code == 200, r.text
    engines = r.json()["detailing_engines"]
    slugs = [e["slug"] for e in engines]
    # Only the in-process built-ins (none = the default) without a live worker —
    # adapy hardcodes NO external engines, so none appear until a pool advertises one.
    assert slugs == ["none", "adapy-default"]
    for e in engines:
        assert e["name"]
        assert isinstance(e["joint_types"], list)
        assert e["origin"] == "code"
    adapy = next(e for e in engines if e["slug"] == "adapy-default")
    assert {"girder_gusset", "column_base_plate", "box_to_box"} <= {j["slug"] for j in adapy["joint_types"]}
    # End plates were dropped from the engine entirely — never advertised.
    assert "beam_column_endplate" not in {j["slug"] for j in adapy["joint_types"]}


def test_external_detailing_engine_discovered_from_live_worker(monkeypatch, tmp_path: pathlib.Path):
    # An external engine appears in the dropdown ONLY while a capability worker
    # advertises it (origin=db, online=True, inprocess=False), carrying its
    # worker_capability + joint types. No live worker -> absent (test above).
    from ada.comms.rest.queue import JobQueue

    monkeypatch.setattr(JobQueue, "enabled", property(lambda self: True))
    monkeypatch.setattr(JobQueue, "connect", lambda self, **kwargs: _async_none())
    monkeypatch.setattr(JobQueue, "list_workers", lambda self: _async_val(_live_worker_advertising_ext_detail()))

    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/api/scopes/shared/procedural-models/detailing-engines")
    assert r.status_code == 200, r.text
    engines = r.json()["detailing_engines"]
    ext = next(e for e in engines if e["slug"] == "ext-detail")
    assert ext["origin"] == "db"
    assert ext["online"] is True
    assert ext["inprocess"] is False
    assert ext["worker_capability"] == "ext-detail"
    assert {"box_to_box", "box_to_plate"} <= {j["slug"] for j in ext["joint_types"]}


def test_builtin_detailing_specs_match_registry():
    # The slim rest catalog mirrors the ada.topo_model registry so the dropdown is
    # never empty without a worker; keep the two definitions in lock-step by slug.
    from ada.comms.rest.catalog import builtin_detailing_engine_specs
    from ada.topo_model import detailing_engine_specs

    slim = {s["slug"] for s in builtin_detailing_engine_specs()}
    registry = {s["slug"] for s in detailing_engine_specs()}
    assert slim == registry == {"none", "adapy-default"}


def test_builtin_detailing_joint_type_fields_match_registry():
    # Phase 3: the advertised joint_types (fields/defaults/ranges) drive the whole
    # Detailing tab, so the slim rest fallback must mirror the registry BY VALUE —
    # not just by slug — or the panel differs when served without a live worker.
    from ada.comms.rest.catalog import builtin_detailing_engine_specs
    from ada.topo_model import detailing_engine_specs

    slim = {s["slug"]: s for s in builtin_detailing_engine_specs()}
    registry = {s["slug"]: s for s in detailing_engine_specs()}
    assert slim["adapy-default"]["joint_types"] == registry["adapy-default"]["joint_types"]
    # And the fields carry the data-driven shape the panel renders from.
    gusset = next(j for j in slim["adapy-default"]["joint_types"] if j["slug"] == "girder_gusset")
    assert gusset["default_enabled"] is True
    field_names = {f["name"] for f in gusset["fields"]}
    assert {"weld_leg", "gusset_t"} <= field_names
    for f in gusset["fields"]:
        assert f["type"] in ("number", "bool", "enum")


# ── blob-key routing: "none" is byte-identical to the plain key ──────


def test_detailing_none_key_is_byte_identical_to_structural():
    # CRITICAL backward-compat: no detailing (None/"none") reduces EXACTLY to the
    # plain structural key, at every lod/engine combination.
    for detailing in (None, "none"):
        assert procedural_detailing_glb_key("m", 3, None, detailing, "sim") == procedural_glb_key("m", 3, None)
        assert procedural_detailing_glb_key("m", 3, None, detailing, "detail") == procedural_detail_glb_key(
            "m", 3, None
        )
        assert procedural_detailing_glb_key("m", 3, "other-engine", detailing, "sim") == procedural_glb_key(
            "m", 3, "other-engine"
        )


def test_detailing_key_gets_det_suffix():
    key = procedural_detailing_glb_key("m", 3, None, "adapy-default", "sim")
    assert key == "_procedural/m/r3.det-adapy.glb"
    # Composes with lod + engine suffixes, all four combos distinct.
    assert (
        procedural_detailing_glb_key("m", 3, None, "adapy-default", "detail") == "_procedural/m/r3_detail.det-adapy.glb"
    )
    assert procedural_detailing_glb_key("m", 3, "echo", "adapy-default", "sim") == "_procedural/m/r3.echo.det-adapy.glb"
    # The .log sibling rule follows the key automatically.
    assert procedural_log_key(key) == "_procedural/m/r3.det-adapy.log"


def test_preview_key_gains_det_fragment():
    assert procedural_preview_glb_key("m", "abc", None, "sim", None) == "_procedural/m/preview/abc.glb"
    assert (
        procedural_preview_glb_key("m", "abc", None, "sim", "adapy-default")
        == "_procedural/m/preview/abc.det-adapy.glb"
    )


def test_detailing_options_change_yields_distinct_key():
    # Phase 3: changing a per-joint option must produce a DISTINCT cache key so the
    # changed detailing never serves stale bytes; empty options keep the plain key.
    base = procedural_detailing_glb_key("m", 3, None, "adapy-default", "sim")
    assert procedural_detailing_glb_key("m", 3, None, "adapy-default", "sim", {}) == base

    k20 = procedural_detailing_glb_key("m", 3, None, "adapy-default", "sim", {"column_base_plate": {"overhang": 20.0}})
    k50 = procedural_detailing_glb_key("m", 3, None, "adapy-default", "sim", {"column_base_plate": {"overhang": 50.0}})
    assert k20 != base and k50 != base and k20 != k50
    # Stable: the SAME options hash the same key (order-independent).
    assert k20 == procedural_detailing_glb_key(
        "m", 3, None, "adapy-default", "sim", {"column_base_plate": {"overhang": 20.0}}
    )
    # The preview key folds options in the same way.
    assert procedural_preview_glb_key(
        "m", "abc", None, "sim", "adapy-default", {"column_base_plate": {"overhang": 20.0}}
    ) != procedural_preview_glb_key("m", "abc", None, "sim", "adapy-default")


def test_detailing_options_never_leak_into_none_key():
    # CRITICAL backward-compat: with no detailing selected the option map has NO
    # effect on the key (byte-identical to the plain structural key).
    for detailing in (None, "none"):
        assert procedural_detailing_glb_key(
            "m", 3, None, detailing, "sim", {"column_base_plate": {"overhang": 20.0}}
        ) == procedural_glb_key("m", 3, None)


def test_structural_ifc_key():
    assert procedural_structural_ifc_key("m", 3) == "_procedural/m/r3.structural.ifc"
    assert procedural_structural_ifc_key("m", 3, "other-engine") == "_procedural/m/r3.other-engine.structural.ifc"


def test_structural_sections_key_is_ifc_sibling():
    from ada.comms.rest.procedural import procedural_structural_sections_key

    # SAME base as the IFC artifact, .structural.ifc -> .structural.sections.json.
    assert procedural_structural_sections_key("m", 3) == "_procedural/m/r3.structural.sections.json"
    assert (
        procedural_structural_sections_key("m", 3, "other-engine")
        == "_procedural/m/r3.other-engine.structural.sections.json"
    )


# ── external-detailing routing: the chained procedural_detail job ────


def test_serialize_structural_artifact_contract():
    # The neutral structural artifact the external detailing side consumes: IFC
    # bytes + a per-Beam section sidecar keyed by member name, section_type = the
    # BOX/… tag the engine matches on, plus the numeric section geometry.
    import ada
    from ada.comms.rest.worker import _serialize_structural_artifact

    bm = ada.Beam("b1", (0, 0, 0), (1, 0, 0), "BOX400x300x20x20")
    part = ada.Assembly("A") / (ada.Part("P") / bm)
    ifc_bytes, sections = _serialize_structural_artifact(part)
    assert ifc_bytes.startswith(b"ISO-10303-21;")  # a real SPF/IFC file
    assert sections["b1"]["section_type"] == "BOX"
    props = sections["b1"]["section_props"]
    assert props["h"] == 0.4 and props["w_top"] == 0.3 and props["t_w"] == 0.02


def test_external_detailing_compile_enqueues_chained_job(monkeypatch, tmp_path: pathlib.Path):
    # An external (Tier-B) detailing compile enqueues TWO jobs: the structural
    # build (writing the neutral artifact) and the chained procedural_detail job
    # routed to the engine's worker_capability. The engine is discovered from a
    # LIVE worker's heartbeat (adapy hardcodes no external engines). Stub the DB +
    # queue at the enqueue boundary (no pg / NATS / external image needed).
    from types import SimpleNamespace

    from ada.comms.rest import db as db_module
    from ada.comms.rest.procedural import (
        procedural_structural_ifc_key,
        procedural_structural_sections_key,
    )
    from ada.comms.rest.queue import JobQueue

    settings = _settings(tmp_path)
    # Enable the queue so the compile endpoint reaches the enqueue; we stub
    # enqueue + list_workers so no NATS is touched (connect is skipped — enabled
    # is forced True without a url so the lifespan's connect() is a no-op-ish path).
    monkeypatch.setattr(JobQueue, "enabled", property(lambda self: True))

    calls: list[dict] = []

    async def _fake_enqueue(self, source_key, target_format="glb", **kw):
        calls.append({"source_key": source_key, "target_format": target_format, **kw})
        return SimpleNamespace(job_id=f"job-{target_format}")

    async def _fake_list_workers(self):
        # A live capability worker advertising the external detailing engine, so the
        # compile endpoint resolves + routes it (its entrypoint/capability come from
        # the heartbeat, not from any hardcoded adapy seed).
        return _live_worker_advertising_ext_detail()

    async def _fake_connect(self, **kwargs):
        return None

    fake_row = {
        "id": "m1",
        "revision": 2,
        "scope_kind": "shared",
        "scope_id": None,
        "engine": None,
        "name": "M",
    }

    async def _fake_get_model(pool, model_id):
        return fake_row

    monkeypatch.setattr(JobQueue, "enqueue", _fake_enqueue)
    monkeypatch.setattr(JobQueue, "list_workers", _fake_list_workers)
    monkeypatch.setattr(JobQueue, "connect", _fake_connect)
    monkeypatch.setattr(db_module, "get_procedural_model", _fake_get_model)

    app = create_app(settings)
    with TestClient(app) as client:
        # _require_procedural_pool only checks non-None; the stubbed DB fns ignore it.
        client.app.state.db_pool = object()
        r = client.post("/api/scopes/shared/procedural-models/m1/compile", params={"detailing": "ext-detail"})
    assert r.status_code == 200, r.text
    body = r.json()

    assert len(calls) == 2
    build = next(c for c in calls if c["target_format"] == "procedural_build")
    detail = next(c for c in calls if c["target_format"] == "procedural_detail")

    ifc_key = procedural_structural_ifc_key("m1", 2, None)
    sections_key = procedural_structural_sections_key("m1", 2, None)

    # Structural stage: runs no in-process detailing but emits the neutral artifact.
    assert build["conversion_options"]["detailing_external"] is True
    assert build["conversion_options"]["detailing"] is None
    assert build["conversion_options"]["structural_ifc_key"] == ifc_key
    assert build["conversion_options"]["structural_sections_key"] == sections_key
    assert build["derived_key"] == "_procedural/m1/r2.glb"  # plain structural key

    # Chained detail stage: routed to the engine's capability with the artifact keys
    # + the entrypoint the live worker advertised.
    assert detail["target_capability"] == "ext-detail"
    assert detail["conversion_options"]["detailing_entrypoint"] == "ext_detailing.adapy:detail"
    assert detail["conversion_options"]["structural_ifc_key"] == ifc_key
    assert detail["conversion_options"]["structural_sections_key"] == sections_key
    assert detail["derived_key"] == "_procedural/m1/r2.det-ext-detail.glb"

    # The endpoint returns the detail job + the detailing GLB key, plus the
    # structural job/key so a caller can load both layers.
    assert body["job_id"] == "job-procedural_detail"
    assert body["derived_key"] == "_procedural/m1/r2.det-ext-detail.glb"
    assert body["structural_job_id"] == "job-procedural_build"
    assert body["structural_key"] == "_procedural/m1/r2.glb"


def test_procedural_detail_waits_for_structural_artifact(monkeypatch):
    # The structural build (a different pool) races the chained procedural_detail
    # job: the artifact may not exist yet when this handler starts. It must WAIT
    # (bounded, interruptible) for the artifact to appear rather than failing on
    # the first miss. Stub storage to 404 the IFC key the first few polls, then
    # succeed, and assert the handler proceeds to write the detailing GLB.
    import asyncio
    import json
    from types import SimpleNamespace

    from ada.comms.rest import worker as worker_mod

    # Tiny poll interval so the test doesn't spend real seconds waiting.
    monkeypatch.setattr(worker_mod, "STRUCTURAL_ARTIFACT_WAIT_INTERVAL_S", 0.01)

    ifc_key = "_procedural/m1/r2.structural.ifc"
    sections_key = "_procedural/m1/r2.structural.sections.json"
    detail_key = "_procedural/m1/r2.det-ext-detail.glb"

    # The IFC artifact only "appears" after this many existence checks; the
    # sidecar is present from the start (written moments after in reality).
    misses_before_ready = 3
    state = {"ifc_checks": 0}
    put_calls: list[dict] = []
    stages: list[str] = []

    class FakeStorage:
        async def exists(self, scope, key):
            if key == ifc_key:
                state["ifc_checks"] += 1
                return state["ifc_checks"] > misses_before_ready
            return True

        async def get_bytes(self, scope, key):
            if key == ifc_key:
                return b"ISO-10303-21;\nDATA;\nENDSEC;\n"
            if key == sections_key:
                return json.dumps({"b1": {"section_type": "BOX", "section_props": {"h": 0.4}}}).encode()
            raise FileNotFoundError(key)

        async def put_bytes(self, scope, key, data, content_encoding=None):
            put_calls.append({"key": key, "data": data, "content_encoding": content_encoding})

    class FakeQueue:
        async def update(self, job_id, **kw):
            if kw.get("stage"):
                stages.append(kw["stage"])

    captured_options: dict = {}

    def _fake_detail(model_bytes, options):
        # The engine entrypoint the pool would resolve; assert it got the artifact.
        assert model_bytes.startswith(b"ISO-10303-21;")
        captured_options.update(options)
        return b"glTF-detailing-layer"

    monkeypatch.setattr("ada.topo_model.engines.load_entrypoint", lambda ep: _fake_detail)

    job = SimpleNamespace(
        job_id="detail-1",
        derived_key=detail_key,
        conversion_options={
            "model_id": "m1",
            "revision": 2,
            "engine": None,
            "detailing": "ext-detail",
            "detailing_entrypoint": "ext_detailing.adapy:detail",
            "structural_ifc_key": ifc_key,
            "structural_sections_key": sections_key,
        },
    )

    asyncio.run(
        worker_mod._run_procedural_detail(
            job=job,
            scope=SimpleNamespace(kind="shared", id=None),
            storage=FakeStorage(),
            queue=FakeQueue(),
            db_pool=None,
            started_at=0.0,
        )
    )

    # It polled past the initial misses (did NOT fail on the first absence) and
    # surfaced the "waiting" stage before proceeding.
    assert state["ifc_checks"] > misses_before_ready
    assert any("waiting" in s for s in stages)
    assert "ready" in stages  # completed
    # The detailing GLB was written gzip-at-rest, and the sidecar reached the engine.
    written = next(c for c in put_calls if c["key"] == detail_key)
    assert written["data"] == b"glTF-detailing-layer"
    assert written["content_encoding"] == "gzip"
    assert captured_options["sections"] == {"b1": {"section_type": "BOX", "section_props": {"h": 0.4}}}
