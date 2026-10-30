"""The /fea/manifest route streams the stored manifest and never parses it.

A large SIN model baked a 148 MB manifest, and the route's get_bytes +
json.loads + JSONResponse peaked a 1 GiB API pod into an OOM kill. These pin
the replacement: the stored (gzip) object goes out as stored, freshness comes
from the bake's ``fea.stamp.json`` -- or, for a bake older than the stamp, from
the manifest's first bytes -- and the combination routes refuse a stale base
without reading it.
"""

from __future__ import annotations

import gzip
import json
import os
import pathlib
import time
import uuid

import pytest
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.converter import (  # noqa: E402
    EXPECTED_FEA_BAKE_VERSION,
    fea_bake_version_from_head,
    fea_stamp_is_current,
)
from ada.comms.rest.routes import fea as fea_routes  # noqa: E402

from .test_fea_manifest import (  # noqa: F401  (env shim runs on import)
    _settings,
    _upload,
)


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def _bake_dir(tmp_path: pathlib.Path, src: str) -> pathlib.Path:
    d = tmp_path / "shared" / f"_derived/{src}.fea"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest(src: str, bake_version: int | None, pad: int = 0) -> dict:
    m: dict = {"version": 2}
    if bake_version is not None:
        m["bake_version"] = bake_version
    m.update({"src": src, "mesh": {"url": "fea.mesh.glb"}, "fields": []})
    if pad:
        # Model-sized payload after the stamps, as the per-element tables were.
        m["groups"] = [{"name": "all", "members": ["EL1"] * pad}]
    return m


def _stage(tmp_path: pathlib.Path, src: str, manifest: dict, *, stamp: dict | None = None) -> bytes:
    """Store ``manifest`` gzip-at-rest (as the worker does) and optionally a stamp."""
    d = _bake_dir(tmp_path, src)
    raw = json.dumps(manifest, separators=(",", ":")).encode()
    (d / "fea.manifest.json").write_bytes(gzip.compress(raw))
    if stamp is not None:
        (d / "fea.stamp.json").write_bytes(gzip.compress(json.dumps(stamp).encode()))
    return raw


def _date(path: pathlib.Path, seconds_ago: float) -> None:
    t = time.time() - seconds_ago
    os.utime(path, (t, t))


@pytest.fixture
def verdicts(monkeypatch) -> list:
    """Every freshness verdict the route reaches (None = fresh)."""
    seen: list = []
    real = fea_routes.fea_manifest_stale_reason

    def spy(*a, **kw):
        seen.append(real(*a, **kw))
        return seen[-1]

    monkeypatch.setattr(fea_routes, "fea_manifest_stale_reason", spy)
    return seen


@pytest.fixture
def no_manifest_parse(monkeypatch):
    """Fail the test if the route json.loads anything bigger than a stamp."""
    real = json.loads

    def guarded(s, *a, **kw):
        assert len(s) < 4096, f"route parsed a {len(s)}-byte document"
        return real(s, *a, **kw)

    monkeypatch.setattr(fea_routes.json, "loads", guarded)


def test_cache_hit_streams_the_stored_gzip_without_parsing(app_client, tmp_path, no_manifest_parse, verdicts):
    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed")
    raw = _stage(
        tmp_path,
        src,
        _manifest(src, EXPECTED_FEA_BAKE_VERSION, pad=20000),
        stamp={"bake_version": EXPECTED_FEA_BAKE_VERSION},
    )
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200, r.text
    assert r.headers["content-encoding"] == "gzip"
    assert r.headers["content-type"].startswith("application/json")
    # httpx inflated it: the body is the stored document byte for byte.
    assert r.content == raw
    assert verdicts == [None]


def test_stamp_decides_freshness_not_the_manifest(app_client, tmp_path, no_manifest_parse, verdicts):
    """The stamp is read, the manifest is not: a manifest whose own head says
    bake_version 0 is served as fresh when its (current) stamp says otherwise,
    and a current-looking manifest under an old stamp is stale."""
    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed")
    _stage(tmp_path, src, _manifest(src, 0), stamp={"bake_version": EXPECTED_FEA_BAKE_VERSION})
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200 and verdicts == [None]

    verdicts.clear()
    _stage(
        tmp_path, src, _manifest(src, EXPECTED_FEA_BAKE_VERSION), stamp={"bake_version": EXPECTED_FEA_BAKE_VERSION - 1}
    )
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    # Queue disabled: the stale bake is served, and the log says why.
    assert r.status_code == 200
    assert verdicts == [f"bake_version {EXPECTED_FEA_BAKE_VERSION - 1} < {EXPECTED_FEA_BAKE_VERSION}"]


def test_stamp_older_than_its_manifest_is_ignored(app_client, tmp_path, no_manifest_parse, verdicts):
    """A stamp left behind by an earlier bake must not vouch for a manifest a
    stamp-less writer put over it: the head of the manifest decides then."""
    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed")
    _stage(tmp_path, src, _manifest(src, 3), stamp={"bake_version": EXPECTED_FEA_BAKE_VERSION})
    d = _bake_dir(tmp_path, src)
    _date(d / "fea.stamp.json", 7200)
    _date(d / "fea.manifest.json", 3600)
    # Keep the source older than both so only the version signal is in play.
    _date(tmp_path / "shared" / src, 9000)
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200
    assert verdicts == [f"bake_version 3 < {EXPECTED_FEA_BAKE_VERSION}"]


def test_legacy_manifest_without_stamp_reads_only_its_head(app_client, tmp_path, no_manifest_parse, verdicts):
    """A pre-stamp bake: bake_version from the first bytes, the rest streamed."""
    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed")
    raw = _stage(tmp_path, src, _manifest(src, 5, pad=200000))
    assert len(raw) > 1_000_000
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200
    assert r.content == raw  # served whole, peeked chunks replayed
    assert verdicts == [f"bake_version 5 < {EXPECTED_FEA_BAKE_VERSION}"]


def test_legacy_manifest_at_current_version_is_fresh(app_client, tmp_path, no_manifest_parse, verdicts):
    src = f"models/{uuid.uuid4().hex}.rmed"
    _upload(app_client, src, b"rmed")
    raw = _stage(tmp_path, src, _manifest(src, EXPECTED_FEA_BAKE_VERSION, pad=50000))
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200 and r.content == raw
    assert verdicts == [None]


def test_head_peek_reads_a_bounded_prefix():
    """The peek inflates at most FEA_MANIFEST_HEAD_BYTES and buffers at most one
    raw chunk past its bound -- and the replay still yields every byte."""
    import asyncio

    from ada.comms.rest.storage import StreamResult

    payload = gzip.compress(json.dumps(_manifest("x", 6, pad=400000)).encode())
    chunks = [payload[i : i + 4096] for i in range(0, len(payload), 4096)]
    pulled = []

    async def gen():
        for c in chunks:
            pulled.append(c)
            yield c

    async def run():
        head, replay = await fea_routes._peek_manifest_head(StreamResult(stream=gen(), content_encoding="gzip"))
        n_pulled = len(pulled)
        out = b"".join([c async for c in replay.stream])
        return head, n_pulled, out

    head, n_pulled, out = asyncio.run(run())
    assert fea_bake_version_from_head(head) == 6
    assert len(head) <= fea_routes.FEA_MANIFEST_HEAD_BYTES
    assert n_pulled < len(chunks) and n_pulled * 4096 <= fea_routes._HEAD_PEEK_MAX_RAW + 4096
    assert out == payload


def test_bake_version_from_head():
    assert fea_bake_version_from_head(b'{"version":2,"bake_version":6,"src":"a"') == 6
    assert fea_bake_version_from_head(b'{\n  "version": 1,\n  "bake_version": 4,') == 4
    assert fea_bake_version_from_head(b'{"version":1,"src":"a","mesh":{}}') == 0


def test_stamp_is_current_rule():
    def h(iso):
        return {"size": 10, "last_modified": iso}

    assert fea_stamp_is_current(h("2026-01-01T00:00:01+00:00"), h("2026-01-01T00:00:00+00:00"))
    assert fea_stamp_is_current(h("2026-01-01T00:00:00+00:00"), h("2026-01-01T00:00:00+00:00"))
    assert not fea_stamp_is_current(h("2026-01-01T00:00:00+00:00"), h("2026-01-01T00:00:05+00:00"))
    assert not fea_stamp_is_current(None, h("2026-01-01T00:00:00+00:00"))
    assert fea_stamp_is_current(h(None), h("2026-01-01T00:00:00+00:00"))


def test_case_route_refuses_a_stale_base_without_parsing_it(app_client, tmp_path, no_manifest_parse):
    src = f"models/{uuid.uuid4().hex}.sin"
    _upload(app_client, src, b"sin")
    _stage(tmp_path, src, _manifest(src, 5, pad=200000))
    r = app_client.get("/api/scopes/shared/fea/case", params={"key": src, "case": 101})
    assert r.status_code == 409
    assert "stale" in r.json()["detail"]


# ── Mesh first: a running bake's preliminary manifest ──────────────────────
#
# While the worker bakes it ships the mesh and a preliminary manifest (stamp
# ``preliminary: true`` + its job id), then fields as they land. The route serves
# that manifest while the job runs -- telling the client so, and which job to
# poll -- and treats it as stale once the job is gone.


@pytest.fixture
def jobs(monkeypatch):
    """Jobs the route's queue knows, by id -> status."""
    from ada.comms.rest.queue import Job, JobQueue

    known: dict[str, str] = {}

    async def get(self, job_id):
        status = known.get(job_id)
        return None if status is None else Job(job_id=job_id, source_key="", derived_key="", status=status)

    monkeypatch.setattr(JobQueue, "enabled", property(lambda self: True))
    monkeypatch.setattr(JobQueue, "get", get)
    return known


def _preliminary(src: str, job_id: str) -> dict:
    return {
        "version": 2,
        "bake_version": EXPECTED_FEA_BAKE_VERSION,
        "preliminary": True,
        "bake_job_id": job_id,
        "src": src,
        "mesh": {"url": "fea.mesh.glb"},
        "fields": [],
    }


def test_running_bakes_preliminary_manifest_is_served_with_its_job(app_client, tmp_path, jobs, verdicts):
    src = f"models/{uuid.uuid4().hex}.sin"
    _upload(app_client, src, b"sin")
    jobs["job-7"] = "running"
    stamp = {"bake_version": EXPECTED_FEA_BAKE_VERSION, "preliminary": True, "job_id": "job-7"}
    raw = _stage(tmp_path, src, _preliminary(src, "job-7"), stamp=stamp)
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200 and r.content == raw
    assert r.headers["x-fea-manifest-state"] == "preliminary"
    assert r.headers["x-fea-bake-job"] == "job-7"

    # The combination routes do not combine from a half-written base.
    r = app_client.get("/api/scopes/shared/fea/case", params={"key": src, "case": 1})
    assert r.status_code == 409 and "still running" in r.json()["detail"]


def test_preliminary_manifest_of_a_dead_bake_is_stale(app_client, tmp_path, jobs, verdicts, monkeypatch):
    src = f"models/{uuid.uuid4().hex}.sin"
    _upload(app_client, src, b"sin")
    jobs["job-8"] = "error"
    stamp = {"bake_version": EXPECTED_FEA_BAKE_VERSION, "preliminary": True, "job_id": "job-8"}
    _stage(tmp_path, src, _preliminary(src, "job-8"), stamp=stamp)
    stale = []
    real = fea_routes._preliminary_stale_reason

    async def spy(ctx, job_id):
        stale.append(await real(ctx, job_id))
        return stale[-1]

    monkeypatch.setattr(fea_routes, "_preliminary_stale_reason", spy)
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert stale and "not running (error)" in stale[0]
    # Stale -> a re-bake is asked for (the test app has no bake queue: 503), and no
    # preliminary headers are ever sent for it.
    assert "x-fea-manifest-state" not in r.headers


def test_preliminary_manifest_without_its_stamp_yet_is_recognised_from_its_head(app_client, tmp_path, jobs, verdicts):
    """Between the manifest's upload and its stamp's, the stamp on disk is the
    previous write's (older): the manifest's head names it preliminary and its job."""
    src = f"models/{uuid.uuid4().hex}.sin"
    _upload(app_client, src, b"sin")
    jobs["job-9"] = "running"
    stamp = {"bake_version": EXPECTED_FEA_BAKE_VERSION, "preliminary": True, "job_id": "job-9"}
    raw = _stage(tmp_path, src, _preliminary(src, "job-9"), stamp=stamp)
    d = _bake_dir(tmp_path, src)
    _date(tmp_path / "shared" / src, 9000)
    _date(d / "fea.stamp.json", 7200)
    _date(d / "fea.manifest.json", 3600)
    r = app_client.get("/api/scopes/shared/fea/manifest", params={"key": src})
    assert r.status_code == 200 and r.content == raw
    assert r.headers["x-fea-bake-job"] == "job-9"
