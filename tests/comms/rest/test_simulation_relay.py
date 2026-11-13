"""A studio with no simulation workers hands its runs to a remote Ada Studio (``simulation_relay``).

The remote is a small FastAPI app that speaks the remote's routes (providers, blob upload and
ranged read, run start / read / cancel) behind an ``httpx.ASGITransport``; the local one is the real
REST app, built with ``simulation_remote`` in its settings.
"""

from __future__ import annotations

import dataclasses
import gzip
import json
import os
import re
import tempfile
from types import SimpleNamespace

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-relay-"))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI, Header, HTTPException, Request, Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada import plugins  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import SimulationRemote  # noqa: E402
from ada.comms.rest.simulation_relay import SimulationRelay  # noqa: E402
from ada.simulation import register_simulation_provider  # noqa: E402
from ada.simulation.runs import new_run_id, new_run_record  # noqa: E402
from ada.simulation.testing import FakeSimulationProvider  # noqa: E402

from .test_simulation_runs import _settings  # noqa: E402

REMOTE_URL = "http://studio.test"
TOKEN = "s3cret"
SUBJECT_KEY = "_dev/connections/x.y/abc123.weld-assembly.json"
ACTIVITY = b"".join(f'{{"n": {i}}}\n'.encode() for i in range(20))


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


def make_remote(*, token: str = TOKEN, fail_start: bool = False) -> tuple[FastAPI, SimpleNamespace]:
    """What a remote Ada Studio answers, and a record of what it was asked."""
    app = FastAPI()
    state = SimpleNamespace(blobs={}, runs={}, started=[], cancelled=[], reads=0)

    def authorise(authorization: str | None) -> None:
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="bad token")

    @app.get("/api/simulation/providers")
    async def providers(authorization: str | None = Header(None)):
        authorise(authorization)
        return {
            "domains": [],
            "providers": [
                {
                    "id": "robot",
                    "domain": "robotics",
                    "engine": "ros",
                    "title": "Welding robot",
                    "version": "1.0",
                    "description": "",
                    "kinds": [{"id": "weld", "title": "Weld", "description": ""}],
                    "parameters_schema": {},
                    "accepts": ["weld-assembly", "not-a-kind-here"],
                    "capability": "sim-robot",
                    "online": True,
                    "workers": 1,
                }
            ],
        }

    @app.put("/api/scopes/{scope}/blobs/{key:path}")
    async def put_blob(scope: str, key: str, request: Request, authorization: str | None = Header(None)):
        authorise(authorization)
        state.blobs[(scope, key)] = await request.body()
        return Response(status_code=201)

    @app.post("/api/scopes/{scope}/simulations")
    async def start(scope: str, body: dict, authorization: str | None = Header(None)):
        authorise(authorization)
        if fail_start:
            raise HTTPException(status_code=409, detail="no live worker serves robot")
        run_id = new_run_id()
        record = new_run_record(
            run_id=run_id,
            provider={"id": body["provider"], "domain": "robotics", "engine": "ros", "version": "1.0"},
            parameters=body.get("parameters") or {},
            inputs=body.get("inputs") or [],
            started_by={"id": "remote-user", "display": "Remote"},
            kind=body.get("kind"),
            subject=body.get("subject"),
        )
        state.runs[run_id] = record
        state.started.append((scope, body))
        return {"run": {**record, "cancel_requested": False}}

    def _advance(run_id: str) -> dict:
        record = state.runs.get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="no such run")
        state.reads += 1
        if record["status"] == "queued":
            key = f"_simulations/{run_id}/artefacts/activity.jsonl"
            state.blobs[("user:me", key)] = ACTIVITY
            record.update(
                status="running",
                stage="simulating",
                progress=0.5,
                artefacts=[{"name": "activity.jsonl", "key": key, "partial": True, "revision": 1}],
            )
        elif record["status"] == "running":
            record.update(status="done", stage="done", progress=1.0, finished_at="2026-01-01T00:00:00Z")
            record["artefacts"][0]["partial"] = False
        return record

    @app.get("/api/scopes/{scope}/simulations/{run_id}")
    async def read(scope: str, run_id: str, authorization: str | None = Header(None)):
        authorise(authorization)
        return {"run": {**_advance(run_id), "cancel_requested": False}}

    @app.post("/api/scopes/{scope}/simulations/{run_id}/cancel")
    async def cancel(scope: str, run_id: str, authorization: str | None = Header(None)):
        authorise(authorization)
        record = state.runs[run_id]
        state.cancelled.append(run_id)
        record.update(status="cancelled", stage="cancelled")
        return {"run": {**record, "cancel_requested": False}, "cancelled": True}

    @app.get("/api/scopes/{scope}/blobs/{key:path}")
    async def get_blob(
        scope: str,
        key: str,
        request: Request,
        range_start: str | None = None,
        range_end: str | None = None,
        authorization: str | None = Header(None),
        range: str | None = Header(None),
    ):
        authorise(authorization)
        data = state.blobs.get((scope, key))
        if data is None:
            raise HTTPException(status_code=404, detail="no such blob")
        header = range or (f"bytes={range_start}-{range_end or ''}" if range_start else None)
        if header is None:
            return Response(data, media_type="application/octet-stream", headers={"Accept-Ranges": "bytes"})
        m = re.fullmatch(r"bytes=(\d+)-(\d*)", header)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else len(data) - 1
        if start >= len(data):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{len(data)}"})
        end = min(end, len(data) - 1)
        return Response(
            data[start : end + 1],
            status_code=206,
            media_type="application/octet-stream",
            headers={"Content-Range": f"bytes {start}-{end}/{len(data)}", "Accept-Ranges": "bytes"},
        )

    return app, state


@pytest.fixture
def remote():
    return make_remote()


def make_client(tmp_path, remote_app: FastAPI | None, *, token: str = TOKEN) -> TestClient:
    settings = dataclasses.replace(
        _settings(tmp_path), simulation_remote=SimulationRemote(url=REMOTE_URL, token=token, scope="user:me")
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=remote_app) if remote_app is not None else httpx.MockTransport(_refuse)
    app.state.simulation_relay = SimulationRelay(settings.simulation_remote, transport=transport)
    return TestClient(app)


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _put_subject(client: TestClient) -> None:
    r = client.put(f"/api/scopes/user:me/blobs/{SUBJECT_KEY}", content=b'{"stand": "in"}')
    assert r.status_code == 201, r.text  # a subject file is accepted by the upload route


def _start(client: TestClient, **extra):
    body = {"provider": "robot", "subject": {"kind": "weld-assembly", "key": SUBJECT_KEY}, **extra}
    return client.post("/api/scopes/user:me/simulations", json=body)


# -- providers ---------------------------------------------------------------------------------


def test_the_remote_providers_are_listed_marked_remote_with_what_they_accept(tmp_path, remote):
    app, _ = remote
    with make_client(tmp_path, app) as client:
        body = client.get("/api/simulation/providers").json()
    (robot,) = body["providers"]
    assert robot["id"] == "robot" and robot["remote"] is True and robot["online"] is True
    assert robot["accepts"] == ["weld-assembly"]  # only the kinds core knows
    assert body["remote"] == {"url": REMOTE_URL, "error": None}


def test_a_local_provider_wins_an_id_clash_and_local_ones_stay_unmarked(tmp_path, remote):
    app, _ = remote
    register_simulation_provider(FakeSimulationProvider("robot", domain="robotics", accepts=()))
    register_simulation_provider(FakeSimulationProvider("fake-sim", domain="fea"))
    with make_client(tmp_path, app) as client:
        providers = {p["id"]: p for p in client.get("/api/simulation/providers").json()["providers"]}
    assert set(providers) == {"robot", "fake-sim"}
    assert "remote" not in providers["robot"] and providers["robot"]["accepts"] == []
    assert "remote" not in providers["fake-sim"]


def test_an_unreachable_remote_lists_nothing_and_says_why(tmp_path):
    register_simulation_provider(FakeSimulationProvider("fake-sim", domain="fea"))
    with make_client(tmp_path, None) as client:
        body = client.get("/api/simulation/providers").json()
    assert [p["id"] for p in body["providers"]] == ["fake-sim"]
    assert REMOTE_URL in body["remote"]["error"] and "connection refused" in body["remote"]["error"]


def test_without_a_remote_nothing_changes(tmp_path):
    register_simulation_provider(FakeSimulationProvider("fake-sim", domain="fea"))
    with TestClient(create_app(_settings(tmp_path))) as client:
        body = client.get("/api/simulation/providers").json()
    assert "remote" not in body and [p["id"] for p in body["providers"]] == ["fake-sim"]


# -- a run -------------------------------------------------------------------------------------


def test_a_run_uploads_its_files_starts_remotely_and_is_followed_through_the_relay(tmp_path, remote):
    app, state = remote
    with make_client(tmp_path, app) as client:
        client.get("/api/simulation/providers")
        _put_subject(client)
        r = _start(client, parameters={"speed": 2}, kind="weld")
        assert r.status_code == 200, r.text
        run = r.json()["run"]
        run_id = run["run_id"]

        # The subject went to the remote scope under the same key, then the run started there.
        assert state.blobs[("user:me", SUBJECT_KEY)] == b'{"stand": "in"}'
        ((scope, sent),) = state.started
        assert scope == "user:me"
        assert sent == {
            "provider": "robot",
            "parameters": {"speed": 2},
            "inputs": [SUBJECT_KEY],
            "kind": "weld",
            "subject": {"kind": "weld-assembly", "key": SUBJECT_KEY},
        }
        # The local record is the remote's, plus where it lives.
        assert run["remote"] == {"url": REMOTE_URL, "scope": "user:me", "run_id": run_id}
        assert run["status"] == "queued" and run_id in state.runs
        assert run["subject"] == {"kind": "weld-assembly", "key": SUBJECT_KEY}

        # Reading it asks the remote; the artefact appears as soon as the remote lists it.
        run = client.get(f"/api/scopes/user:me/simulations/{run_id}").json()["run"]
        assert run["status"] == "running" and run["progress"] == 0.5
        (artefact,) = run["artefacts"]
        blob = f"/api/scopes/user:me/blobs/{artefact['key']}"

        # The live view reads in byte ranges: the header, or the query a proxy cannot strip.
        part = client.get(blob, headers={"Range": "bytes=2-9"})
        assert part.status_code == 206 and part.content == ACTIVITY[2:10]
        assert part.headers["content-range"] == f"bytes 2-9/{len(ACTIVITY)}"
        tail = client.get(blob, params={"range_start": 10, "range_end": ""})
        assert tail.status_code == 206 and tail.content == ACTIVITY[10:]
        assert client.get(blob, params={"range_start": len(ACTIVITY), "range_end": ""}).status_code == 416
        assert client.get(blob).content == ACTIVITY
        assert client.get(f"/api/scopes/user:me/blobs/_simulations/{run_id}/artefacts/none.bin").status_code == 404

        # The listing refreshes it too, and a finished run is kept locally: the remote is not asked again.
        (listed,) = client.get("/api/scopes/user:me/simulations").json()["runs"]
        assert listed["status"] == "done" and listed["remote"]["run_id"] == run_id
        reads = state.reads
        again = client.get(f"/api/scopes/user:me/simulations/{run_id}").json()["run"]
        assert again["status"] == "done" and again["artefacts"][0]["partial"] is False
        assert state.reads == reads


def test_cancel_is_the_remotes(tmp_path, remote):
    app, state = remote
    with make_client(tmp_path, app) as client:
        client.get("/api/simulation/providers")
        _put_subject(client)
        run_id = _start(client).json()["run"]["run_id"]
        r = client.post(f"/api/scopes/user:me/simulations/{run_id}/cancel")
        assert r.status_code == 200 and r.json()["cancelled"] is True
        assert state.cancelled == [run_id]
        assert r.json()["run"]["status"] == "cancelled"
        assert client.get(f"/api/scopes/user:me/simulations/{run_id}").json()["run"]["status"] == "cancelled"


def test_the_remotes_refusals_are_502s_that_name_it(tmp_path):
    app, _ = make_remote(fail_start=True)
    with make_client(tmp_path, app) as client:
        client.get("/api/simulation/providers")
        _put_subject(client)
        r = _start(client)
    assert r.status_code == 502
    assert REMOTE_URL in r.json()["detail"] and "no live worker serves robot" in r.json()["detail"]


def test_a_wrong_token_is_a_502_not_a_listing(tmp_path, remote):
    app, _ = remote
    with make_client(tmp_path, app, token="wrong") as client:
        body = client.get("/api/simulation/providers").json()
    assert body["providers"] == [] and "401" in body["remote"]["error"] and REMOTE_URL in body["remote"]["error"]


def test_a_run_is_not_started_without_its_subject_file(tmp_path, remote):
    app, state = remote
    with make_client(tmp_path, app) as client:
        client.get("/api/simulation/providers")
        r = _start(client)  # nothing uploaded locally
    assert r.status_code == 404 and not state.started


def test_a_lost_remote_leaves_the_stored_run_listed_and_a_single_read_a_502(tmp_path, remote):
    app, _ = remote
    with make_client(tmp_path, app) as client:
        client.get("/api/simulation/providers")
        _put_subject(client)
        run_id = _start(client).json()["run"]["run_id"]
        client.app.state.simulation_relay = SimulationRelay(
            SimulationRemote(url=REMOTE_URL, token=TOKEN), transport=httpx.MockTransport(_refuse)
        )
        (listed,) = client.get("/api/scopes/user:me/simulations").json()["runs"]
        assert listed["run_id"] == run_id and "connection refused" in listed["remote_error"]
        r = client.get(f"/api/scopes/user:me/simulations/{run_id}")
        assert r.status_code == 502 and REMOTE_URL in r.json()["detail"]
        assert (
            client.get(f"/api/scopes/user:me/blobs/_simulations/{run_id}/artefacts/activity.jsonl").status_code == 502
        )


def test_the_settings_read_the_remote_from_the_environment(monkeypatch):
    from ada.comms.rest.config import load_settings

    monkeypatch.delenv("ADA_SIMULATION_REMOTE_URL", raising=False)
    assert load_settings().simulation_remote is None
    monkeypatch.setenv("ADA_SIMULATION_REMOTE_URL", "https://studio.example.com/")
    monkeypatch.setenv("ADA_SIMULATION_REMOTE_TOKEN", " tok ")
    remote = load_settings().simulation_remote
    assert remote == SimulationRemote(url="https://studio.example.com", token="tok", scope="user:me")
    monkeypatch.setenv("ADA_SIMULATION_REMOTE_SCOPE", "project:p1")
    assert load_settings().simulation_remote.scope == "project:p1"


def test_an_upload_of_a_subject_file_is_accepted_by_the_blob_route_and_other_json_is_not(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        ok = client.put("/api/scopes/user:me/blobs/_dev/a.weld-assembly.json", content=gzip.compress(b"{}"))
        assert ok.status_code == 201
        assert client.put("/api/scopes/user:me/blobs/_dev/a.json", content=b"{}").status_code == 415
        assert json.loads(client.get("/api/scopes/user:me/blobs/_dev/a.weld-assembly.json").content) == {}
