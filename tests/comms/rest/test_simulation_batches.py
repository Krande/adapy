"""Batches of simulation runs (``ada.simulation.batches``): a run started with ``batch`` is indexed
under it, the batches are listed with their counts, a batch's runs are paged, the runs of no batch
are listed without the batched ones, and runs started before batches existed are put into one
after the fact (``simulation_backfill``)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import time

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-sim-batches-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from obstore.store import LocalStore  # noqa: E402

from ada import plugins  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.simulation_backfill import backfill_batch  # noqa: E402
from ada.comms.rest.simulation_runs import (  # noqa: E402
    SETTLED,
    batch_index,
    read_run,
    write_run,
)
from ada.comms.rest.storage import Storage  # noqa: E402
from ada.simulation import register_simulation_provider  # noqa: E402
from ada.simulation.batches import (  # noqa: E402
    BatchError,
    check_batch,
    summarise_batch,
)
from ada.simulation.runs import new_run_id, new_run_record  # noqa: E402
from ada.simulation.testing import FakeSimulationProvider  # noqa: E402

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"
PROVIDER = "fake-sim"
BATCH = {"id": "group-7-20261121T1419", "label": "Frame A", "source": {"kind": "group", "key": "group-7"}}


@pytest.fixture(autouse=True)
def _clean():
    plugins.reset_registry()
    SETTLED.clear()
    yield
    plugins.reset_registry()
    SETTLED.clear()


def _settings(path) -> Settings:
    return Settings(
        storage_kind="local",
        s3=None,
        local=LocalConfig(path=str(path), prefix=""),
        host="127.0.0.1",
        port=0,
        static_path="",
        queue=QueueConfig(
            url=None, stream="ada", subject="ada.viewer.jobs.convert", kv_bucket="ada-viewer-jobs", durable="w"
        ),
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


@pytest.fixture
def client(tmp_path):
    (tmp_path / "store" / SCOPE_DIR).mkdir(parents=True)
    with TestClient(create_app(_settings(tmp_path / "store"))) as c:
        yield c


def _start(client, **body):
    r = client.post(f"/api/scopes/{SCOPE}/simulations", json={"provider": PROVIDER, "parameters": {"steps": 1}, **body})
    assert r.status_code == 200, r.text
    return r.json()["run"]


def _wait_ended(client, run_id, timeout_s=60.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        run = client.get(f"/api/scopes/{SCOPE}/simulations/{run_id}").json()["run"]
        if run["status"] in ("done", "error", "cancelled"):
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not end")


def _list(client, **params) -> dict:
    r = client.get(f"/api/scopes/{SCOPE}/simulations", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ── the batch itself ─────────────────────────────────────────────────────────────────────────────


def test_a_batch_is_checked_and_a_bare_id_is_its_own_label():
    assert check_batch(None) is None
    assert check_batch("nightly-3") == {"id": "nightly-3", "label": "nightly-3", "source": None}
    assert check_batch(BATCH) == BATCH
    assert check_batch({"id": "a", "label": "  A  "})["label"] == "A"
    for bad in (
        {"id": ""},
        {"id": "has space"},
        {"id": "-lead"},
        {"id": "a", "label": ""},
        7,
        {"id": "a", "source": {"kind": "file", "key": "x"}},
        {"id": "a", "source": {"kind": "node"}},
    ):
        with pytest.raises(BatchError):
            check_batch(bad)


def test_a_summary_counts_each_status_and_leaves_cancelled_runs_out_of_the_progress():
    def rec(status, created, progress=0.0):
        return {
            "status": status,
            "created_at": created,
            "progress": progress,
            "provider": {"id": "p", "domain": "robotics"},
        }

    s = summarise_batch(
        check_batch(BATCH),
        [
            rec("done", "2026-01-01T00:00:01Z"),
            rec("error", "2026-01-01T00:00:02Z"),
            rec("running", "2026-01-01T00:00:03Z", 0.5),
            rec("queued", "2026-01-01T00:00:04Z"),
            rec("cancelled", "2026-01-01T00:00:00Z"),
        ],
    )
    assert s["count"] == 5 and s["active"] == 2
    assert s["counts"] == {"done": 1, "running": 1, "queued": 1, "error": 1, "cancelled": 1}
    assert s["progress"] == pytest.approx(2.5 / 4)
    assert (s["newest_at"], s["oldest_at"]) == ("2026-01-01T00:00:04Z", "2026-01-01T00:00:00Z")
    assert s["provider"] == {"id": "p", "domain": "robotics"} and s["label"] == "Frame A"


# ── over the routes ──────────────────────────────────────────────────────────────────────────────


def test_runs_started_in_a_batch_are_listed_as_one_and_paged(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    batched = []
    for _ in range(3):
        run = _start(client, batch=BATCH)
        assert run["batch"] == BATCH
        batched.append(run["run_id"])
        _wait_ended(client, run["run_id"])
        time.sleep(0.003)
    alone = _start(client)["run_id"]
    _wait_ended(client, alone)
    assert _list(client)["runs"][0]["batch"] is None

    body = client.get(f"/api/scopes/{SCOPE}/simulations/batches").json()
    assert body["unbatched"] == 1
    (line,) = body["batches"]
    assert (line["id"], line["label"], line["source"], line["count"]) == (BATCH["id"], "Frame A", BATCH["source"], 3)
    assert line["counts"]["done"] == 3 and line["active"] == 0 and line["progress"] == 1.0
    assert line["providers"] == [{"id": PROVIDER, "domain": "robotics"}]
    one = client.get(f"/api/scopes/{SCOPE}/simulations/batches/{BATCH['id']}").json()["batch"]
    assert one["count"] == 3

    # A batch's runs, newest first, a page at a time.
    page = _list(client, batch=BATCH["id"], limit=2)
    assert [r["run_id"] for r in page["runs"]] == batched[::-1][:2] and page["next"] == batched[1]
    rest = _list(client, batch=BATCH["id"], limit=2, before=page["next"])
    assert [r["run_id"] for r in rest["runs"]] == [batched[0]] and rest["next"] is None
    # The runs of no batch, without the batch's.
    assert [r["run_id"] for r in _list(client, batch="none")["runs"]] == [alone]
    # Everything, as before.
    assert [r["run_id"] for r in _list(client)["runs"]] == [alone, *batched[::-1]]


def test_a_status_filter_and_bad_arguments(client):
    register_simulation_provider(FakeSimulationProvider(PROVIDER))
    ok = _start(client, batch="b1")["run_id"]
    _wait_ended(client, ok)
    bad = _start(client, batch="b1", parameters={"steps": 2, "fail_at": 1})["run_id"]
    assert _wait_ended(client, bad)["status"] == "error"
    assert [r["run_id"] for r in _list(client, batch="b1", status="done")["runs"]] == [ok]
    assert [r["run_id"] for r in _list(client, batch="b1", status="error,cancelled")["runs"]] == [bad]
    url = f"/api/scopes/{SCOPE}/simulations"
    assert client.get(url, params={"status": "finished"}).status_code == 400
    assert client.get(url, params={"batch": "no spaces"}).status_code == 400
    assert client.get(f"{url}/batches/nothing-here").status_code == 404
    # A batch that is not one is refused before anything is queued.
    r = client.post(url, json={"provider": PROVIDER, "parameters": {"steps": 1}, "batch": {"id": "a b"}})
    assert r.status_code == 400 and "batch.id" in r.json()["detail"]
    assert client.get(f"{url}/batches").json()["batches"][0]["counts"] == {
        "done": 1,
        "running": 0,
        "queued": 0,
        "error": 1,
        "cancelled": 0,
    }


# ── after the fact ───────────────────────────────────────────────────────────────────────────────


def test_runs_from_before_batches_are_put_into_one_and_a_dry_run_writes_nothing(tmp_path):
    storage = Storage(LocalStore(str(tmp_path)), prefix="")
    scope = Scope(kind="user", id="local-dev")
    provider = {"id": "robot", "domain": "robotics", "engine": "fake"}

    async def seed():
        ids = {}
        for label, status in (
            ("frame j1", "done"),
            ("frame j2", "queued"),
            ("frame v2 j1", "queued"),
            ("framing", "done"),
            ("other", "done"),
        ):
            rec = new_run_record(
                run_id=new_run_id(), provider=provider, parameters={"label": label}, inputs=[], started_by={"id": "t"}
            )
            rec["status"] = status
            await write_run(storage, scope, rec)
            ids[label] = rec["run_id"]
            await asyncio.sleep(0.002)
        return ids

    ids = asyncio.run(seed())
    kwargs = dict(
        batch={"id": "frame-welds", "label": "Frame welds"},
        provider="robot",
        label_prefix="frame ",
        exclude_label_prefixes=["frame v2 "],
    )

    dry = asyncio.run(backfill_batch(storage, scope, dry=True, **kwargs))
    assert (dry.matched, dry.stamped, dry.indexed) == (2, 2, 2) and dry.by_status == {"done": 1, "queued": 1}
    assert asyncio.run(batch_index(storage, scope)) == {}
    assert asyncio.run(read_run(storage, scope, ids["frame j1"]))["batch"] is None

    done = asyncio.run(backfill_batch(storage, scope, dry=False, **kwargs))
    assert (done.stamped, done.indexed) == (2, 2)
    assert asyncio.run(batch_index(storage, scope)) == {
        "frame-welds": sorted([ids["frame j1"], ids["frame j2"]], reverse=True)
    }
    assert asyncio.run(read_run(storage, scope, ids["frame j2"]))["batch"]["label"] == "Frame welds"
    assert asyncio.run(read_run(storage, scope, ids["frame v2 j1"]))["batch"] is None

    again = asyncio.run(backfill_batch(storage, scope, dry=False, **kwargs))
    assert (again.stamped, again.indexed, again.already) == (0, 0, 2)
    # In another batch: left there unless asked to move.
    other = asyncio.run(backfill_batch(storage, scope, dry=True, **{**kwargs, "batch": "else"}))
    assert other.elsewhere == 2 and other.stamped == 0
