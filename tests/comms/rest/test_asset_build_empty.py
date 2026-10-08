"""A builder that finds nothing to draw (``NothingToBuild``) ends its job DONE with an ``empty``
summary at the derived key and a SKIPPED audit row -- never an error, so nothing files it as a bug,
and a repeat of the same request reads the stored answer instead of building again."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from ada.assets.build import NothingToBuild, parse_build_summary
from ada.assets.builders import clear_asset_builders, register_asset_builder
from ada.comms.rest.formats import asset_build

DERIVED_KEY = "_derived/assets/fixture-lines/fixture-a/pump-b/20260921T143001Z/pump-b/eba9599c6db77cfb/summary.json"


class _EmptyBuilder:
    def build(self, options, **kwargs):
        raise NothingToBuild("none of the 3 leaves holds an element this builder draws")


class _BrokenBuilder:
    def build(self, options, **kwargs):
        raise RuntimeError("builder exploded")


class _Queue:
    def __init__(self):
        self.updates = []

    async def update(self, job_id, **fields):
        self.updates.append(fields)


class _Storage:
    def __init__(self):
        self.blobs = {}

    async def put_bytes(self, scope, key, data, **kwargs):
        self.blobs[key] = data


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_asset_builders()
    yield
    clear_asset_builders()


def _run(builder, monkeypatch):
    register_asset_builder("asset-build-probe", builder)
    audits = []

    async def _audit_done(db_pool, job_id, status, error, started_at, traceback=None, metrics=None):
        audits.append((status, error))

    monkeypatch.setattr(asset_build, "_audit_done", _audit_done)
    job = SimpleNamespace(
        job_id="job-1",
        derived_key=DERIVED_KEY,
        conversion_options={
            "capability": "asset-build-probe",
            "provider": "fixture-lines",
            "collection": "fixture-a",
            "subject": "pump-b",
            "revision": "20260921T143001Z",
            "node": "pump-b",
            "fingerprint": "eba9599c6db77cfb",
            "hierarchy_source": "20260921T143001Z",
            "options": {},
        },
    )
    queue, storage = _Queue(), _Storage()
    asyncio.run(
        asset_build._run_asset_build(job=job, scope=None, storage=storage, queue=queue, db_pool=None, started_at=0.0)
    )
    return queue, storage, audits


def test_nothing_to_build_is_stored_as_an_empty_answer_and_skipped(monkeypatch):
    queue, storage, audits = _run(_EmptyBuilder, monkeypatch)

    assert queue.updates[-1]["status"] == "done"
    assert audits == [("skipped", "none of the 3 leaves holds an element this builder draws")]
    summary = parse_build_summary(json.loads(storage.blobs[DERIVED_KEY]))
    assert summary.empty and not summary.ok and summary.glb_key == ""
    assert summary.provenance.fingerprint == "eba9599c6db77cfb"
    assert summary.provenance.node == "pump-b"


def test_any_other_failure_is_still_an_error(monkeypatch):
    queue, storage, audits = _run(_BrokenBuilder, monkeypatch)

    assert queue.updates[-1]["status"] == "error"
    assert audits[0][0] == "error"
    assert DERIVED_KEY not in storage.blobs
