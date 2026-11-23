"""A non-finite number (``inf``, ``nan``) in a run record must not break the simulation routes.

JSON has no spelling for them; Python writes ``Infinity`` / ``NaN`` and the API's strict encoder
refuses them, so one such number in one stored record used to turn the scope's whole run listing
into a 500. Records are now written strict (the number stored as null, with a warning), an older
record that has one reads as null, and the routes answer one as null whatever its source.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-simulations-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada import plugins  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.routes.simulations import FiniteJSONResponse  # noqa: E402
from ada.simulation import register_simulation_provider  # noqa: E402
from ada.simulation.runs import (  # noqa: E402
    STATUS_DONE,
    dump_run_record,
    finite_json,
    new_run_id,
    new_run_record,
    parse_run_record,
)
from ada.simulation.testing import FakeSimulationProvider  # noqa: E402

from .test_simulation_runs import (  # noqa: E402
    PROVIDER,
    SCOPE,
    SCOPE_DIR,
    _settings,
    _ungzip,
    _wait_ended,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


@pytest.fixture
def client(tmp_path):
    scope_dir = tmp_path / "store" / SCOPE_DIR
    scope_dir.mkdir(parents=True)
    with TestClient(create_app(_settings(tmp_path / "store"))) as c:
        c.scope_dir = scope_dir
        yield c


def _finished_record(summary: dict, parameters: dict | None = None) -> dict:
    record = new_run_record(
        run_id=new_run_id(),
        provider={"id": "some-provider", "domain": "robotics", "engine": "test"},
        parameters=parameters or {},
        inputs=[],
        started_by={"id": "tester"},
    )
    return {**record, "status": STATUS_DONE, "stage": STATUS_DONE, "progress": 1.0, "summary": summary}


class _Warnings(logging.Handler):
    """The warnings logged under ``ada`` (whose loggers may not propagate to caplog's root handler)."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.messages)


@pytest.fixture
def warnings_logged():
    handler = _Warnings()
    logger = logging.getLogger("ada.simulation")
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


def _store_lenient(client, record: dict) -> None:
    """Store ``record`` as a writer from before this fix did: ``Infinity`` / ``NaN`` in the JSON."""
    run_dir = client.scope_dir / "_simulations" / record["run_id"]
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_bytes(json.dumps(record, sort_keys=True).encode("utf-8"))


def test_finite_json_nulls_each_non_finite_number_and_names_where_it_was():
    value = {"a": 1.5, "b": {"c": math.inf, "d": [0, -math.inf, {"e": math.nan}]}, "f": "inf", "g": None}
    clean, paths = finite_json(value, "summary")
    assert clean == {"a": 1.5, "b": {"c": None, "d": [0, None, {"e": None}]}, "f": "inf", "g": None}
    assert paths == ["summary.b.c", "summary.b.d[1]", "summary.b.d[2].e"]
    assert finite_json({"ok": [1, 2.0, True]}) == ({"ok": [1, 2.0, True]}, [])


def test_a_stored_record_with_inf_and_nan_lists_and_views_with_them_as_null(client):
    good = _finished_record({"weld": {"rms_m": 0.002}})
    bad = _finished_record(
        {"weld": {"rms_m": math.inf, "max_m": -math.inf, "passes": [{"err": math.nan}, {"err": 0.1}]}},
        parameters={"tolerance": math.inf},
    )
    _store_lenient(client, good)
    _store_lenient(client, bad)
    assert b"Infinity" in (client.scope_dir / "_simulations" / bad["run_id"] / "run.json").read_bytes()

    r = client.get(f"/api/scopes/{SCOPE}/simulations")
    assert r.status_code == 200, r.text
    runs = {run["run_id"]: run for run in r.json()["runs"]}
    assert set(runs) == {good["run_id"], bad["run_id"]}
    assert runs[good["run_id"]]["summary"] == {"weld": {"rms_m": 0.002}}
    assert runs[bad["run_id"]]["summary"] == {
        "weld": {"rms_m": None, "max_m": None, "passes": [{"err": None}, {"err": 0.1}]}
    }

    r = client.get(f"/api/scopes/{SCOPE}/simulations/{bad['run_id']}")
    assert r.status_code == 200, r.text
    run = r.json()["run"]
    assert run["summary"]["weld"]["rms_m"] is None and run["parameters"] == {"tolerance": None}


def test_the_response_class_answers_a_non_finite_number_as_null():
    assert json.loads(FiniteJSONResponse({"x": [math.inf, 1.0]}).body) == {"x": [None, 1.0]}
    assert json.loads(FiniteJSONResponse({"x": 1.0}).body) == {"x": 1.0}


def test_writing_a_record_stores_strict_json_and_warns_naming_the_run_and_fields(warnings_logged):
    record = _finished_record({"weld": {"rms_m": math.inf}, "ok": 1.0})
    raw = dump_run_record(record)
    assert b"Infinity" not in raw and b"NaN" not in raw
    stored = json.loads(raw)
    assert stored["summary"] == {"weld": {"rms_m": None}, "ok": 1.0}
    assert record["summary"]["weld"]["rms_m"] == math.inf  # the caller's record is left as it was
    assert record["run_id"] in warnings_logged.text and "summary.weld.rms_m" in warnings_logged.text
    assert parse_run_record(raw)["summary"] == stored["summary"]


def test_a_provider_reporting_inf_in_its_summary_finishes_done_with_null_stored(client, warnings_logged):
    class InfSummary(FakeSimulationProvider):
        def run(self, case, ctx):
            result = super().run(case, ctx)
            return dataclasses.replace(result, summary={"weld": {"rms_m": math.inf}, "steps": 1})

    register_simulation_provider(InfSummary(PROVIDER))
    r = client.post(f"/api/scopes/{SCOPE}/simulations", json={"provider": PROVIDER, "parameters": {"steps": 1}})
    assert r.status_code == 200, r.text
    run_id = r.json()["run"]["run_id"]
    run = _wait_ended(client, run_id)
    assert run["status"] == "done", run
    assert run["summary"] == {"weld": {"rms_m": None}, "steps": 1}
    on_disk = _ungzip((client.scope_dir / "_simulations" / run_id / "run.json").read_bytes())
    assert b"Infinity" not in on_disk
    assert json.loads(on_disk)["summary"]["weld"]["rms_m"] is None
    assert run_id in warnings_logged.text and "summary.weld.rms_m" in warnings_logged.text
    listed = client.get(f"/api/scopes/{SCOPE}/simulations")
    assert listed.status_code == 200 and listed.json()["runs"][0]["summary"]["weld"]["rms_m"] is None
