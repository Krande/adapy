"""The job-scoped storage routes (``/api/jobs/{job_id}/storage/...``) and the multipart they drive.

Against a filesystem store, so everything goes through the API (``via: "api"``, the staged
multipart): what the job may read and write, small objects both ways, a presigned-style download,
a single upload, and a multipart upload that is interrupted, resumed from the parts the store
holds, completed, or aborted. The store's own multipart (``S3Multipart``) is driven against a fake
S3 endpoint, and the SigV4 re-signing it relies on is pinned against obstore's own signature for
each method.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import tempfile
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-job-storage-"))

import httpx  # noqa: E402
import obstore as obs  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from obstore.store import S3Store  # noqa: E402

from ada import plugins  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
    WorkerGrantsConfig,
)
from ada.comms.rest.job_grants import (  # noqa: E402
    GrantService,
    MemoryGrantBindings,
    mint_grant,
)
from ada.comms.rest.multipart import STAGING_PREFIX, PartInfo, S3Multipart  # noqa: E402
from ada.comms.rest.presign import resign_with_params  # noqa: E402
from ada.comms.rest.queue import Job  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.storage import Storage  # noqa: E402
from ada.simulation import declare_storage_read_prefixes  # noqa: E402
from ada.simulation.runs import (  # noqa: E402
    cancel_key,
    dump_run_record,
    new_run_id,
    new_run_record,
    run_prefix,
    run_record_key,
)

SECRET = "job-storage-test-secret-0123456789abcdef"
CFG = WorkerGrantsConfig(secret=SECRET, part_size=1024, small_object_max=2048, max_size=100_000)
PROVIDER = "fake-sim"
CAP = "sim-fake-sim"
SCOPE = Scope.project("p1")


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}

    async def get(self, job_id):
        return self.jobs.get(job_id)


def _settings(path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(url=None, stream="s", subject="ada.viewer.jobs.convert", kv_bucket="k", durable="d"),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
        worker_grants=CFG,
    )


class Harness:
    def __init__(self, client: TestClient, service: GrantService, jobs: FakeJobs, root) -> None:
        self.client, self.service, self.jobs, self.root = client, service, jobs, root
        self.storage: Storage = service.storage

    def run(self, coro):
        return asyncio.run(coro)

    def new_run(self, job_id: str = "job-1", inputs=("models/cell.json",)) -> dict:
        record = new_run_record(
            run_id=new_run_id(),
            provider={"id": PROVIDER, "domain": "robotics", "engine": "fake"},
            parameters={},
            inputs=list(inputs),
            started_by={"id": "tester"},
        )
        key = run_record_key(record["run_id"])
        self.run(self.storage.put_bytes(SCOPE, key, dump_run_record(record), content_encoding="gzip"))
        self.jobs.jobs[job_id] = Job(
            job_id=job_id,
            source_key=key,
            derived_key=key,
            status="running",
            target_format="simulation_run",
            scope_kind="project",
            scope_id="p1",
            conversion_options={"provider_id": PROVIDER, "run_id": record["run_id"]},
            target_capability=CAP,
        )
        return record

    def token(self, job_id: str = "job-1", worker: str = "ws-01") -> str:
        reply = self.run(
            self.service.answer(f"ada.viewer.grants.{CAP}.{worker}", json.dumps({"job_id": job_id}).encode())
        )
        assert reply["ok"], reply
        return reply["token"]

    def call(self, method: str, path: str, token: str, job_id: str = "job-1", **kw):
        url = path if path.startswith("/") else f"/api/jobs/{job_id}/storage/{path}"
        return self.client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kw)

    def stored(self, key: str) -> bytes:
        return (self.root / SCOPE.prefix() / key).read_bytes()


@pytest.fixture
def h(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    app = create_app(_settings(root))
    with TestClient(app) as client:
        jobs = FakeJobs()
        service = GrantService(CFG, app.state.rest.storage, jobs.get, MemoryGrantBindings())
        app.state.job_grants = service
        yield Harness(client, service, jobs, root)


# ── who may call ─────────────────────────────────────────────────────────────────────────────────


def test_the_routes_are_not_there_without_job_grants(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    with TestClient(create_app(_settings(root))) as client:
        resp = client.get("/api/jobs/j/storage/object", params={"key": "a"}, headers={"Authorization": "Bearer x"})
    assert resp.status_code == 404 and "not enabled" in resp.json()["detail"]


def test_a_call_without_a_grant_is_refused(h):
    h.new_run()
    assert h.client.get("/api/jobs/job-1/storage/object", params={"key": "x"}).status_code == 401
    assert h.call("GET", "object", "not-a-token", params={"key": "x"}).status_code == 401
    other, _ = mint_grant(
        WorkerGrantsConfig(secret="x" * 40), job_id="job-1", scope="projects/p1", cap=CAP, worker="ws-01"
    )
    assert h.call("GET", "object", other, params={"key": "x"}).status_code == 401


def test_a_grant_is_useless_on_another_jobs_routes(h):
    record = h.new_run("job-1")
    h.new_run("job-2")
    token = h.token("job-1")
    resp = h.call("GET", "object", token, job_id="job-2", params={"key": run_record_key(record["run_id"])})
    assert resp.status_code == 403 and "another job" in json.dumps(resp.json())


def test_a_second_worker_cannot_use_a_bound_job(h):
    h.new_run()
    h.token(worker="ws-01")
    forged, _ = mint_grant(CFG, job_id="job-1", scope="projects/p1", cap=CAP, worker="ws-02")
    resp = h.call("GET", "list", forged, params={"prefix": "_simulations/"})
    assert resp.status_code == 403 and resp.json()["detail"]["error"] == "claimed_by_another_worker"


# ── reads ────────────────────────────────────────────────────────────────────────────────────────


def test_reads_are_the_record_the_inputs_and_the_cancel_marker_only(h):
    record = h.new_run()
    run_id = record["run_id"]
    h.run(h.storage.put_bytes(SCOPE, "models/cell.json", b'{"cell": 1}'))
    h.run(h.storage.put_bytes(SCOPE, "models/secret.json", b"{}"))
    token = h.token()
    got = h.call("GET", "object", token, params={"key": run_record_key(run_id)})
    assert got.status_code == 200 and json.loads(got.content)["run_id"] == run_id  # gzip at rest undone
    assert h.call("GET", "object", token, params={"key": "models/cell.json"}).content == b'{"cell": 1}'
    assert h.call("GET", "object", token, params={"key": cancel_key(run_id)}).status_code == 404
    assert h.call("GET", "object", token, params={"key": "models/secret.json"}).status_code == 403
    assert h.call("GET", "object", token, params={"key": "../users/x/y"}).status_code == 400
    assert h.call("GET", "list", token, params={"prefix": "models/"}).status_code == 403
    assert h.call("GET", "list", token, params={"prefix": ""}).status_code == 403


def test_a_declared_read_prefix_can_be_listed_and_read(h):
    declare_storage_read_prefixes(PROVIDER, ["assets/robots/"])
    h.new_run()
    h.run(h.storage.put_bytes(SCOPE, "assets/robots/arm/arm.urdf", b"<robot/>"))
    token = h.token()
    listed = h.call("GET", "list", token, params={"prefix": "assets/robots/"}).json()
    assert [k["key"] for k in listed["keys"]] == ["assets/robots/arm/arm.urdf"]
    assert h.call("GET", "object", token, params={"key": "assets/robots/arm/arm.urdf"}).content == b"<robot/>"
    # Read, never write.
    assert h.call("PUT", "object", token, params={"key": "assets/robots/arm/x"}, content=b"x").status_code == 403


def test_a_large_object_is_read_through_its_download_url(h):
    record = h.new_run()
    key = f"{run_prefix(record['run_id'])}artefacts/big.bin"
    data = os.urandom(5000)
    h.run(h.storage.put_bytes(SCOPE, key, data))
    token = h.token()
    assert h.call("GET", "object", token, params={"key": key}).status_code == 413
    url = h.call("GET", "download-url", token, params={"key": key}).json()
    assert url["via"] == "api" and url["size"] == 5000 and url["expires_in"] == CFG.ttl_s
    assert h.call("GET", url["url"], token).content == data
    assert (
        h.call("GET", "download-url", token, params={"key": f"{run_prefix(record['run_id'])}nope"}).status_code == 404
    )


# ── writes ───────────────────────────────────────────────────────────────────────────────────────


def test_small_writes_land_in_the_run_directory_only(h):
    record = h.new_run()
    token = h.token()
    key = run_record_key(record["run_id"])
    body = dump_run_record({**record, "status": "running"})
    resp = h.call("PUT", "object", token, params={"key": key, "content_encoding": "gzip"}, content=body)
    assert resp.status_code == 200
    assert h.stored(key)[:2] == b"\x1f\x8b" and json.loads(gzip.decompress(h.stored(key)))["status"] == "running"
    assert h.call("PUT", "object", token, params={"key": "models/cell.json"}, content=b"x").status_code == 403
    other = f"_simulations/{new_run_id()}/run.json"
    assert h.call("PUT", "object", token, params={"key": other}, content=b"x").status_code == 403
    too_big = h.call("PUT", "object", token, params={"key": f"{run_prefix(record['run_id'])}x"}, content=b"x" * 3000)
    assert too_big.status_code == 413


def test_a_file_up_to_the_part_size_is_one_put(h):
    record = h.new_run()
    token = h.token()
    key = f"{run_prefix(record['run_id'])}artefacts/model.glb"
    plan = h.call("POST", "upload", token, json={"key": key, "size": 1000}).json()
    assert plan["mode"] == "single" and plan["via"] == "api"
    assert h.call("PUT", plan["url"], token, content=b"g" * 1000).status_code == 200
    assert h.stored(key) == b"g" * 1000
    assert h.call("POST", "upload", token, json={"key": key, "size": 10**9}).status_code == 413


def _staged_files(h: Harness, upload_id: str) -> list:
    """What is left of an upload's staging (a filesystem store keeps the emptied directory)."""
    return [p for p in (h.root / SCOPE.prefix() / STAGING_PREFIX / upload_id).rglob("*") if p.is_file()]


def test_a_multipart_upload_resumes_from_the_parts_the_store_holds(h):
    record = h.new_run()
    token = h.token()
    key = f"{run_prefix(record['run_id'])}artefacts/timeline.mcap"
    data = os.urandom(2500)
    plan = h.call("POST", "upload", token, json={"key": key, "size": len(data)}).json()
    assert plan["mode"] == "multipart" and plan["part_count"] == 3 and plan["part_size"] == 1024
    urls = {p["part_number"]: p["url"] for p in plan["parts"]}
    assert sorted(urls) == [1, 2, 3]

    def part(n):
        return data[(n - 1) * 1024 : n * 1024]

    etags = {}
    for n in (1, 2):  # ... and then the worker is interrupted
        resp = h.call("PUT", urls[n], token, content=part(n))
        assert resp.status_code == 200
        etags[n] = resp.json()["etag"]
    # Resume: the store says which parts it holds; fresh URLs for the rest.
    resumed = h.call(
        "POST", "upload/parts", token, json={"key": key, "upload_id": plan["upload_id"], "part_numbers": [3]}
    ).json()
    assert [(p["part_number"], p["size"]) for p in resumed["uploaded"]] == [(1, 1024), (2, 1024)]
    assert [p["etag"] for p in resumed["uploaded"]] == [etags[1], etags[2]]
    assert [p["part_number"] for p in resumed["parts"]] == [3]
    etags[3] = h.call("PUT", resumed["parts"][0]["url"], token, content=part(3)).json()["etag"]

    parts = [{"part_number": n, "etag": etags[n]} for n in (1, 2, 3)]
    done = h.call("POST", "upload/complete", token, json={"key": key, "upload_id": plan["upload_id"], "parts": parts})
    assert done.status_code == 200 and done.json() == {"key": key, "size": 2500, "parts": 3}
    assert h.stored(key) == data
    assert _staged_files(h, plan["upload_id"]) == []


def test_a_multipart_upload_is_not_completed_with_a_part_missing_or_changed(h):
    record = h.new_run()
    token = h.token()
    key = f"{run_prefix(record['run_id'])}artefacts/a.bin"
    plan = h.call("POST", "upload", token, json={"key": key, "size": 2048 + 10}).json()
    upload_id = plan["upload_id"]
    urls = {p["part_number"]: p["url"] for p in plan["parts"]}
    e1 = h.call("PUT", urls[1], token, content=b"a" * 1024).json()["etag"]
    h.call("PUT", urls[3], token, content=b"c" * 10)
    gap = h.call("POST", "upload/complete", token, json={"key": key, "upload_id": upload_id})
    assert gap.status_code == 409 and "1..n" in gap.json()["detail"]
    wrong = [{"part_number": 1, "etag": '"not-it"'}]
    assert (
        h.call("POST", "upload/complete", token, json={"key": key, "upload_id": upload_id, "parts": wrong}).status_code
        == 409
    )
    missing = [{"part_number": 1, "etag": e1}, {"part_number": 2, "etag": "x"}]
    assert (
        h.call(
            "POST", "upload/complete", token, json={"key": key, "upload_id": upload_id, "parts": missing}
        ).status_code
        == 409
    )
    # An upload id is only good for its own key.
    other = f"{run_prefix(record['run_id'])}artefacts/b.bin"
    assert h.call("POST", "upload/parts", token, json={"key": other, "upload_id": upload_id}).status_code == 404
    # Abort removes what was staged.
    assert h.call("POST", "upload/abort", token, json={"key": key, "upload_id": upload_id}).json()["aborted"]
    assert _staged_files(h, upload_id) == []
    assert h.call("POST", "upload/parts", token, json={"key": key, "upload_id": upload_id}).status_code == 404


def test_a_part_larger_than_the_part_size_is_refused(h):
    record = h.new_run()
    token = h.token()
    key = f"{run_prefix(record['run_id'])}artefacts/a.bin"
    plan = h.call("POST", "upload", token, json={"key": key, "size": 3000}).json()
    resp = h.call("PUT", plan["parts"][0]["url"], token, content=b"x" * 2000)
    assert resp.status_code == 413


def test_writes_and_grants_leave_audit_rows_with_the_workers_name(h, monkeypatch):
    rows = []

    async def insert_audit(pool, **kw):
        rows.append(kw)

    monkeypatch.setattr("ada.comms.rest.db.insert_audit", insert_audit)
    h.service._pool = lambda: object()
    record = h.new_run()
    token = h.token(worker="ws-07")
    key = f"{run_prefix(record['run_id'])}artefacts/x.json"
    h.call("PUT", "object", token, params={"key": key}, content=b"{}")
    assert [(r["action"], r["user_sub"], r["status"]) for r in rows] == [
        ("job_grant", "worker:ws-07", "ok"),
        ("job_storage_put", "worker:ws-07", "ok"),
    ]
    assert rows[1]["key"] == key and rows[1]["job_id"] == "job-1" and rows[1]["write_bytes"] == 2


# ── the store's own multipart, against a fake S3 ─────────────────────────────────────────────────

S3_SECRET = "s3-test-secret"


def _s3_storage() -> Storage:
    store = S3Store(
        "bkt",
        endpoint="http://s3.test:9000",
        allow_http=True,
        region="garage",
        access_key_id="AKTEST",
        secret_access_key=S3_SECRET,
    )
    return Storage(store, prefix="base", presign_secret=S3_SECRET)


@pytest.mark.parametrize("method", ["PUT", "GET"])
def test_resigning_for_a_method_reproduces_obstores_signature(method):
    store = _s3_storage()._store
    url = obs.sign(store, method, "a/b c.bin", timedelta(seconds=900))
    resigned = resign_with_params(url, S3_SECRET, {}, method=method)
    sig = parse_qs(urlsplit(url).query)["X-Amz-Signature"][0]
    assert parse_qs(urlsplit(resigned).query)["X-Amz-Signature"][0] == sig
    # The method is part of what is signed.
    other = "GET" if method == "PUT" else "PUT"
    assert parse_qs(urlsplit(resign_with_params(url, S3_SECRET, {}, method=other)).query)["X-Amz-Signature"][0] != sig


class FakeS3:
    """Just enough of S3's multipart API to drive ``S3Multipart``, recording each call."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.parts = {1: ('"e1"', 10), 2: ('"e2"', 4)}
        self.completed: bytes | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode(), keep_blank_values=True).items()}
        assert "X-Amz-Signature" in q and request.url.path == "/bkt/base/projects/p1/out.bin"
        assert "Authorization" not in request.headers
        self.calls.append((request.method, q))
        if request.method == "POST" and "uploads" in q:
            return httpx.Response(
                200, text="<InitiateMultipartUploadResult><UploadId>up-1</UploadId></InitiateMultipartUploadResult>"
            )
        if q.get("uploadId") != "up-1":
            return httpx.Response(404, text="<Error><Code>NoSuchUpload</Code></Error>")
        if request.method == "GET":
            parts = "".join(
                f"<Part><PartNumber>{n}</PartNumber><ETag>{e}</ETag><Size>{s}</Size></Part>"
                for n, (e, s) in self.parts.items()
            )
            return httpx.Response(
                200, text=f"<ListPartsResult><IsTruncated>false</IsTruncated>{parts}</ListPartsResult>"
            )
        if request.method == "POST":
            self.completed = request.content
            return httpx.Response(200, text="<CompleteMultipartUploadResult/>")
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(400)


def test_the_stores_multipart_is_driven_with_signed_requests():
    storage = _s3_storage()
    assert storage.can_sign_requests
    fake = FakeS3()
    mp = S3Multipart(storage, transport=httpx.MockTransport(fake))

    async def run():
        upload_id = await mp.create(SCOPE, "out.bin")
        url = await mp.part_url(SCOPE, "out.bin", upload_id, 2, 900)
        parts = await mp.list_parts(SCOPE, "out.bin", upload_id)
        await mp.complete(SCOPE, "out.bin", upload_id, parts)
        await mp.abort(SCOPE, "out.bin", "gone")  # an upload already gone is not an error
        return upload_id, url, parts

    upload_id, url, parts = asyncio.run(run())
    assert upload_id == "up-1"
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert (q["partNumber"], q["uploadId"]) == ("2", "up-1") and "X-Amz-Signature" in q
    assert parts == [PartInfo(1, '"e1"', 10), PartInfo(2, '"e2"', 4)]
    assert [c[0] for c in fake.calls] == ["POST", "GET", "POST", "DELETE"]
    assert fake.completed == (
        b'<CompleteMultipartUpload><Part><PartNumber>1</PartNumber><ETag>"e1"</ETag></Part>'
        b'<Part><PartNumber>2</PartNumber><ETag>"e2"</ETag></Part></CompleteMultipartUpload>'
    )


def test_a_store_without_its_secret_falls_back_to_staged_parts():
    from ada.comms.rest.multipart import StagedMultipart, multipart_for

    store = S3Store(
        "bkt", endpoint="http://s3.test:9000", allow_http=True, region="r", access_key_id="A", secret_access_key="B"
    )
    assert isinstance(multipart_for(Storage(store, prefix="")), StagedMultipart)
