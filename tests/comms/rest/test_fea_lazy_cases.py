"""The lazy load-combination routes (``/fea/case``, ``/fea/envelope``) and jobs
(``fea_case``, ``fea_envelope``).

The routes are the API half: cache hit, 202 with a job, the 404/409 guards and
the staleness rule, against a stub job transport. The handlers run end to end
against local storage holding a real base bake of a synthetic deck: Tier A from
ranged reads of the stored strides, and the raw path from the source.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import pathlib
import tempfile
import time
import uuid

import numpy as np
import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.converter import (  # noqa: E402
    EXPECTED_FEA_BAKE_VERSION,
    fea_case_stale_reason,
)

from .test_fea_manifest import _settings, _stage_manifest, _upload  # noqa: E402

HASH = "ab12cd34" + "0" * 56


def _manifest(src: str, *, bake_version: int = EXPECTED_FEA_BAKE_VERSION) -> dict:
    return {
        "version": 2,
        "bake_version": bake_version,
        "src": src,
        "mesh": {},
        "fields": [{"name_canonical": "sesam.nodes.displacement", "components": ["X"], "steps": []}],
        "baked_steps": [1, 2],
        "combination_steps": [
            {
                "n": 101,
                "name": "comb101",
                "complex": False,
                "terms": [[1, 1.2, 0.0], [2, 1.1, 0.0]],
                "coefficients": [[1.2, 0.0], [1.1, 0.0]],
                "needs_raw": False,
                "recipe_hash": HASH,
            }
        ],
        "lazy_cases": {"version": 1, "server": True, "client_tier_a": True, "cases_prefix": "cases/"},
    }


@dataclasses.dataclass
class _Submitted:
    job_id: str
    status: str = "queued"
    progress: float = 0.0
    stage: str = "queued"


class _Jobs:
    """A job transport that records what it was asked to run."""

    def __init__(self) -> None:
        self.requests: list = []

    def supports(self, feature: str) -> bool:
        return True

    def require(self, feature: str) -> None:
        return None

    async def submit(self, req, *, before_dispatch=None):
        self.requests.append(req)
        return _Submitted(job_id=f"job-{len(self.requests)}")


@pytest.fixture
def client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as c:
        jobs = _Jobs()
        app.state.rest = dataclasses.replace(app.state.rest, jobs=jobs)
        c.jobs = jobs
        yield c


def _stage_case(tmp_path: pathlib.Path, src: str, overlay: dict) -> pathlib.Path:
    target = tmp_path / "shared" / f"_derived/{src}.fea/cases/101-{HASH[:8]}/fea.case.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(overlay))
    return target


def _source(client: TestClient, tmp_path: pathlib.Path, *, manifest: dict | None = None) -> str:
    src = f"models/{uuid.uuid4().hex}.SIN"
    _upload(client, src, b"sin-bytes")
    _stage_manifest(tmp_path, src, manifest or _manifest(src))
    return src


def test_a_case_not_yet_materialised_queues_a_job(client: TestClient, tmp_path: pathlib.Path):
    src = _source(client, tmp_path)
    r = client.get("/api/scopes/shared/fea/case", params={"key": src, "case": 101})
    assert r.status_code == 202, r.text
    body = r.json()
    case_key = f"_derived/{src}.fea/cases/101-{HASH[:8]}/fea.case.json"
    assert body == {
        "job_id": "job-1",
        "status": "queued",
        "progress": 0.0,
        "stage": "queued",
        "source_key": src,
        "case": 101,
        "case_key": case_key,
    }
    (req,) = client.jobs.requests
    assert (req.target_format, req.step, req.derived_key, req.force_rebuild) == ("fea_case", 101, case_key, False)
    assert req.feature == "bake"


def test_a_materialised_case_is_served_with_its_prefix(client: TestClient, tmp_path: pathlib.Path):
    src = _source(client, tmp_path)
    overlay = {"version": 1, "kind": "fea_case", "bake_version": 4, "case": {"n": 101, "recipe_hash": HASH}}
    _stage_case(tmp_path, src, overlay)
    r = client.get("/api/scopes/shared/fea/case", params={"key": src, "case": 101, "field": "sesam.nodes.displacement"})
    assert r.status_code == 200, r.text
    assert r.json() == {**overlay, "prefix": f"_derived/{src}.fea/cases/101-{HASH[:8]}/"}
    assert client.jobs.requests == []


def test_a_case_older_than_its_base_bake_is_rebuilt(client: TestClient, tmp_path: pathlib.Path):
    src = _source(client, tmp_path)
    case = _stage_case(tmp_path, src, {"bake_version": 4, "case": {"n": 101, "recipe_hash": HASH}})
    past = time.time() - 3600
    os.utime(case, (past, past))
    r = client.get("/api/scopes/shared/fea/case", params={"key": src, "case": 101})
    assert r.status_code == 202, r.text
    assert client.jobs.requests[0].force_rebuild is True


def test_case_guards(client: TestClient, tmp_path: pathlib.Path):
    src = _source(client, tmp_path)
    assert client.get("/api/scopes/shared/fea/case", params={"key": src, "case": 7}).status_code == 404
    r = client.get("/api/scopes/shared/fea/case", params={"key": src, "case": 101, "field": "nope"})
    assert r.status_code == 404
    # No base bake to combine from: the viewer asks for the manifest instead.
    bare = f"models/{uuid.uuid4().hex}.SIN"
    _upload(client, bare, b"sin-bytes")
    assert client.get("/api/scopes/shared/fea/case", params={"key": bare, "case": 101}).status_code == 409
    # A base bake the manifest route would re-bake is no base to combine from.
    old = _source(client, tmp_path, manifest=_manifest("x", bake_version=3))
    r = client.get("/api/scopes/shared/fea/case", params={"key": old, "case": 101})
    assert r.status_code == 409 and "stale" in r.json()["detail"]
    missing = f"models/{uuid.uuid4().hex}.SIN"
    assert client.get("/api/scopes/shared/fea/case", params={"key": missing, "case": 101}).status_code == 404
    glb = f"models/{uuid.uuid4().hex}.glb"
    _upload(client, glb, b"glTF")
    assert client.get("/api/scopes/shared/fea/case", params={"key": glb, "case": 101}).status_code == 415
    assert client.jobs.requests == []


def test_envelope_route(client: TestClient, tmp_path: pathlib.Path):
    src = _source(client, tmp_path)
    field = "sesam.nodes.displacement"
    r = client.get("/api/scopes/shared/fea/envelope", params={"key": src, "field": field})
    assert r.status_code == 202, r.text
    (req,) = client.jobs.requests
    env_key = f"_derived/{src}.fea/envelopes/{field}/fea.envelope.json"
    assert (req.target_format, req.field_name, req.derived_key) == ("fea_envelope", field, env_key)
    target = tmp_path / "shared" / env_key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"bake_version": 4, "field": field, "cases": [101], "scalar_range": {"X": [0, 1]}}))
    r = client.get("/api/scopes/shared/fea/envelope", params={"key": src, "field": field})
    assert r.status_code == 200 and r.json()["cases"] == [101]
    assert client.get("/api/scopes/shared/fea/envelope", params={"key": src, "field": "nope"}).status_code == 404


def _head(iso):
    return {"size": 1, "last_modified": iso, "e_tag": None}


def test_case_stale_reason():
    fresh = {"bake_version": EXPECTED_FEA_BAKE_VERSION}
    t0, t1 = "2026-09-01T10:00:00+00:00", "2026-09-02T10:00:00+00:00"
    assert fea_case_stale_reason(fresh, _head(t0), _head(t1), _head(t0)) is None
    assert fea_case_stale_reason(fresh, _head(t0), _head(t0), _head(t1)) == "base bake newer than case"
    assert fea_case_stale_reason(fresh, _head(t1), _head(t0), _head(t0)) == "source newer than bake"
    assert fea_case_stale_reason({"bake_version": 3}, None, None, None)
    assert fea_case_stale_reason(fresh, _head(None), _head(t0), _head("junk")) is None


# ── the jobs, end to end against local storage ──────────────────────────────


class _Queue:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    async def update(self, job_id, **kw):
        self.updates.append(kw)


@pytest.fixture(scope="module")
def stored_base(tmp_path_factory):
    """A synthetic deck's lazy base bake, uploaded to local storage as the worker does."""
    from tests.core.fem.results.lazy_cases.synthetic_sin import build_synthetic_deck

    from ada.comms.rest.scope import Scope
    from ada.comms.rest.storage import Storage
    from ada.fem.results.artefacts import bake_fea_artefacts_from_source

    root = tmp_path_factory.mktemp("lazy-rest")
    deck = build_synthetic_deck(root / "deck.SIN", nx=8, ny=6, n_tri_cols=2, web_rows=2, n_combinations=8)
    bake = bake_fea_artefacts_from_source(deck.path, root / "base")
    (root / "store").mkdir()
    storage = Storage.from_settings(_settings(root / "store"))
    scope = Scope.shared()
    src = "models/deck.SIN"

    async def upload():
        await storage.put_path(scope, src, deck.path)
        for f in sorted(bake.out_dir.iterdir()):
            enc = "gzip" if f.suffix == ".json" else None
            await storage.put_path(scope, f"_derived/{src}.fea/{f.name}", f, content_encoding=enc)

    asyncio.run(upload())
    manifest = json.loads(bake.manifest_path.read_text())
    return storage, scope, src, root, manifest


def _run_job(handler_fn, storage, scope, src, **job_kw):
    from ada.comms.rest.queue import Job

    queue = _Queue()
    job = Job(job_id="j", source_key=src, status="queued", **job_kw)
    asyncio.run(handler_fn(job=job, scope=scope, storage=storage, queue=queue, db_pool=None, started_at=0.0))
    return queue


@pytest.mark.parametrize("raw", [False, True], ids=["tier-a", "raw-records"])
def test_the_case_job_writes_the_case_the_route_serves(stored_base, raw):
    from ada.comms.rest.formats.fea import _run_fea_case
    from ada.fem.results.artefacts.combine import (
        case_dir_name,
        local_stride_fetcher,
        superpose_case,
    )

    storage, scope, src, root, manifest = stored_base
    entry = next(e for e in manifest["combination_steps"] if bool(e["needs_raw"]) is raw)
    prefix = f"_derived/{src}.fea/cases/{case_dir_name(entry)}/"
    queue = _run_job(_run_fea_case, storage, scope, src, derived_key=prefix + "fea.case.json", step=entry["n"])
    assert queue.updates[-1]["status"] == "done", queue.updates

    async def read(key):
        return await storage.get_bytes(scope, key)

    overlay = json.loads(asyncio.run(read(prefix + "fea.case.json")))
    assert overlay["producer"]["tier"] == ("raw" if raw else "A")
    assert overlay["case"]["recipe_hash"] == entry["recipe_hash"]
    if raw:
        return
    want = superpose_case(manifest, local_stride_fetcher(root / "base"), entry["n"]).arrays
    for field in overlay["fields"]:
        for b in field.get("per_type") or [{"elem_type": None, "blob": field["blob"]}]:
            data = asyncio.run(read(prefix + b["blob"]["url"]))
            got = np.frombuffer(data[b["blob"]["header_bytes"] :], dtype="<f4")
            assert np.array_equal(got, want[(field["name_canonical"], b["elem_type"])].ravel(), equal_nan=True)


def test_the_envelope_job(stored_base):
    from ada.comms.rest.formats.fea import _run_fea_envelope
    from ada.fem.results.artefacts.combine import local_stride_fetcher, superpose_case

    storage, scope, src, root, manifest = stored_base
    field = "sesam.nodes.displacement"
    key = f"_derived/{src}.fea/envelopes/{field}/fea.envelope.json"
    queue = _run_job(_run_fea_envelope, storage, scope, src, derived_key=key, field=field)
    assert queue.updates[-1]["status"] == "done", queue.updates
    doc = json.loads(asyncio.run(storage.get_bytes(scope, key)))
    tier_a = [e["n"] for e in manifest["combination_steps"] if not e["needs_raw"]]
    assert doc["cases"] == tier_a
    assert sorted(doc["skipped"]) == sorted(e["n"] for e in manifest["combination_steps"] if e["needs_raw"])
    stack = np.stack(
        [
            superpose_case(manifest, local_stride_fetcher(root / "base"), n, [field]).arrays[(field, None)]
            for n in tier_a
        ]
    )
    for c, comp in enumerate(doc["components"]):
        assert doc["scalar_range"][comp] == [float(np.nanmin(stack[..., c])), float(np.nanmax(stack[..., c]))]
