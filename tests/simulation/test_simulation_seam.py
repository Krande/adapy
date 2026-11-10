"""The simulation provider seam on its own (``ada.simulation``): registering a provider, checking
parameters against its schema, and one run through ``execute_run`` against storage kept in a dict.

No REST app and no queue: what a provider is handed and what a run leaves behind are the same
whichever process runs it, so they are pinned here once.
"""

from __future__ import annotations

import json
import threading

import pytest

from ada import plugins
from ada.simulation import (
    SIMULATION_DOMAINS,
    ParameterError,
    SimulationCancelled,
    SimulationResult,
    allowed_artefact_roles,
    get_simulation_provider,
    register_simulation_provider,
    simulation_provider_specs,
    validate_parameters,
)
from ada.simulation.runner import execute_run
from ada.simulation.runs import (
    artefact_key,
    cancel_key,
    new_run_id,
    new_run_record,
    parse_run_record,
    run_id_of_key,
    run_record_key,
    safe_artefact_name,
)
from ada.simulation.testing import FakeSimulationProvider


@pytest.fixture(autouse=True)
def _clean_registry():
    plugins.reset_registry()
    yield
    plugins.reset_registry()


class DictStorage:
    """The scope-bound storage a run is handed, kept in a dict."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.lock = threading.Lock()

    def get_bytes(self, key):
        with self.lock:
            if key not in self.blobs:
                raise FileNotFoundError(key)
            return self.blobs[key]

    def put_bytes(self, key, data, content_encoding=None):
        with self.lock:
            self.blobs[key] = bytes(data)

    def put_path(self, key, src):
        with open(src, "rb") as fh:
            self.put_bytes(key, fh.read())

    def fetch_to_path(self, key, dest):
        with open(dest, "wb") as fh:
            fh.write(self.get_bytes(key))
        return dest

    def record(self, run_id) -> dict:
        return parse_run_record(self.get_bytes(run_record_key(run_id)))


def _record(provider, parameters=None, inputs=()):
    spec = {"id": provider.id, "domain": provider.domain, "engine": provider.engine, "version": "1.0.0"}
    params = validate_parameters(provider.describe().parameters_schema, parameters or {"steps": 3})
    return new_run_record(
        run_id=new_run_id(), provider=spec, parameters=params, inputs=list(inputs), started_by={"id": "tester"}
    )


# ── the kinds of simulation ──────────────────────────────────────────


def test_the_domains_are_a_closed_set_with_their_roles():
    assert list(SIMULATION_DOMAINS) == ["fea", "robotics", "cfd"]
    required = {d.id: [r.id for r in d.artefact_roles if r.required] for d in SIMULATION_DOMAINS.values()}
    assert required == {"fea": ["result"], "robotics": ["model", "timeline"], "cfd": ["mesh", "fields"]}
    for domain in SIMULATION_DOMAINS.values():
        assert domain.title and domain.description
        # Every domain may also use the common roles.
        assert {"log", "report", "data"} <= set(allowed_artefact_roles(domain.id))


# ── registration ─────────────────────────────────────────────────────


def test_registering_a_provider_lists_it_and_requests_its_capability():
    provider = FakeSimulationProvider("fake-sim", domain="fea", engine="fake")
    register_simulation_provider(provider)
    assert get_simulation_provider("fake-sim") is provider
    (spec,) = simulation_provider_specs()
    assert (spec["id"], spec["domain"], spec["engine"], spec["capability"]) == (
        "fake-sim",
        "fea",
        "fake",
        "sim-fake-sim",
    )
    assert spec["title"] == "Fake simulation" and spec["kinds"][0]["id"] == "count"
    assert spec["parameters_schema"]["properties"]["steps"]["type"] == "integer"
    # The worker serves the pool through the same request any plugin makes after boot.
    assert plugins.requested_worker_capabilities() == ["sim-fake-sim"]
    # ada.plugins is the door a plugin registers through.
    assert plugins.register_simulation_provider is register_simulation_provider


@pytest.mark.parametrize("domain", ["robot", "", "FEA", "thermal"])
def test_an_unknown_domain_is_rejected_at_registration(domain):
    with pytest.raises(ValueError) as exc:
        register_simulation_provider(FakeSimulationProvider("odd", domain=domain))
    if domain:
        assert "fea, robotics, cfd" in str(exc.value)
    assert get_simulation_provider("odd") is None
    assert plugins.requested_worker_capabilities() == []


@pytest.mark.parametrize("provider_id", ["Fake", "a.b", "a b", "-a", "x" * 41, "a/b"])
def test_a_provider_id_must_be_a_queue_subject_token(provider_id):
    # The id becomes the pool name `sim-<id>`; "a.b" and "a-b" must not end up sharing one.
    with pytest.raises(ValueError):
        register_simulation_provider(FakeSimulationProvider(provider_id))
    assert simulation_provider_specs() == []


def test_registering_an_id_again_replaces_the_provider():
    register_simulation_provider(FakeSimulationProvider("fake"))
    second = FakeSimulationProvider("fake", domain="cfd")
    register_simulation_provider(second)
    assert get_simulation_provider("fake") is second
    assert [s["domain"] for s in simulation_provider_specs()] == ["cfd"]


def test_something_that_is_not_a_provider_is_refused():
    class NoRun:
        id, domain, engine = "x", "fea", "y"

        def describe(self): ...

    with pytest.raises(TypeError):
        register_simulation_provider(NoRun())


def test_a_provider_whose_describe_fails_is_left_out_of_the_listing():
    class Broken(FakeSimulationProvider):
        def describe(self):
            raise RuntimeError("no licence")

    register_simulation_provider(Broken("broken"))
    register_simulation_provider(FakeSimulationProvider("fine"))
    assert [s["id"] for s in simulation_provider_specs()] == ["fine"]


# ── parameters ───────────────────────────────────────────────────────

SCHEMA = FakeSimulationProvider().describe().parameters_schema


def test_valid_parameters_come_back_with_the_schema_defaults():
    assert validate_parameters(SCHEMA, {"steps": 4}) == {"steps": 4, "duration": 0.0, "label": "plain"}


@pytest.mark.parametrize(
    "parameters,problem",
    [
        ({}, "steps: is required"),
        ({"steps": "3"}, "steps: expected integer, got str"),
        ({"steps": 2.5}, "steps: expected integer"),
        ({"steps": True}, "steps: expected integer"),
        ({"steps": 0}, "steps: must be at least 1"),
        ({"steps": 5000}, "steps: must be at most 1000"),
        ({"steps": 2, "label": "loud"}, "label: must be one of"),
        ({"steps": 2, "speed": 1}, "speed: is not a parameter of this provider"),
        ([1, 2], "parameters: expected object"),
    ],
)
def test_parameters_that_do_not_fit_are_named(parameters, problem):
    with pytest.raises(ParameterError) as exc:
        validate_parameters(SCHEMA, parameters)
    assert problem in str(exc.value)


def test_every_problem_is_reported_at_once():
    with pytest.raises(ParameterError) as exc:
        validate_parameters(SCHEMA, {"steps": 0, "duration": -1})
    assert len(exc.value.problems) == 2


def test_nested_objects_arrays_and_strings_are_checked():
    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 2, "pattern": "^[a-z]+$"},
            "gravity": {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3},
            "solver": {"type": "object", "properties": {"tol": {"type": "number", "exclusiveMinimum": 0}}},
        },
    }
    ok = {"name": "ab", "gravity": [0, 0, -9.81], "solver": {"tol": 1e-6}}
    assert validate_parameters(schema, ok) == ok
    with pytest.raises(ParameterError) as exc:
        validate_parameters(schema, {"name": "A", "gravity": [0, "x"], "solver": {"tol": 0}})
    text = str(exc.value)
    for part in ("name: must be at least 2", "gravity: needs at least 3", "gravity[1]: expected number", "solver.tol"):
        assert part in text


def test_no_schema_accepts_any_object():
    assert validate_parameters(None, {"anything": [1]}) == {"anything": [1]}
    assert validate_parameters({}, None) == {}


# ── run keys ─────────────────────────────────────────────────────────


def test_a_run_keeps_its_files_in_its_own_directory_under_derived():
    run_id = new_run_id()
    assert run_record_key(run_id) == f"_simulations/{run_id}/run.json"
    assert artefact_key(run_id, "frames/0001.bin") == f"_simulations/{run_id}/artefacts/frames/0001.bin"
    assert cancel_key(run_id).startswith(f"_simulations/{run_id}/")
    assert run_id_of_key(run_record_key(run_id)) == run_id
    assert run_id_of_key(artefact_key(run_id, "a.json")) is None


@pytest.mark.parametrize("name", ["../x", "/abs", "a//b", "", ".hidden", "a b", "a/../b"])
def test_an_artefact_name_cannot_leave_the_run_directory(name):
    with pytest.raises(ValueError):
        safe_artefact_name(name)


def test_run_ids_sort_by_creation_time():
    import time

    first = new_run_id()
    time.sleep(0.005)
    assert first < new_run_id()
    with pytest.raises(ValueError):
        run_record_key("../../etc")


# ── one run ──────────────────────────────────────────────────────────


def test_a_run_reports_progress_stores_artefacts_and_leaves_a_record():
    storage = DictStorage()
    storage.put_bytes("models/arm.glb", b"12345")
    provider = FakeSimulationProvider()
    record = _record(provider, {"steps": 3}, inputs=["models/arm.glb"])
    progress: list[tuple[float, str]] = []
    artefact_states: list[tuple[list[dict], bool]] = []

    finished = execute_run(
        provider,
        record,
        storage,
        scope="shared",
        on_progress=lambda f, s: progress.append((f, s)),
        on_artefacts=lambda a, c: artefact_states.append((a, c)),
    )

    assert finished["status"] == "done" and finished["error"] is None
    assert finished["progress"] == 1.0
    assert finished["started_at"] and finished["finished_at"]
    assert finished["summary"] == {"steps": 3, "inputs": {"models/arm.glb": 5}}
    assert [round(f, 2) for f, _ in progress] == [0.33, 0.67, 1.0]
    assert progress[-1][1] == "step 3/3"

    by_name = {a["name"]: a for a in finished["artefacts"]}
    assert set(by_name) == {"timeline.json", "model.json", "log.txt"}
    assert (by_name["timeline.json"]["role"], by_name["model.json"]["role"], by_name["log.txt"]["role"]) == (
        "timeline",
        "model",
        "log",
    )
    assert not any(a["partial"] for a in finished["artefacts"])
    # Stored three times while running and once at the end.
    assert by_name["timeline.json"]["revision"] == 4
    assert by_name["timeline.json"]["media_type"] == "application/json"
    assert json.loads(storage.get_bytes(by_name["timeline.json"]["key"])) == {"steps": [{"step": i} for i in (1, 2, 3)]}
    assert storage.get_bytes(by_name["log.txt"]["key"]).decode().splitlines() == [f"step {i} of 3" for i in (1, 2, 3)]

    # The first artefact state a follower saw was the partial timeline; the last one is complete.
    first, complete = artefact_states[0]
    assert complete is False and first[0]["name"] == "timeline.json" and first[0]["partial"] is True
    assert artefact_states[-1][1] is True
    # What is in storage is what was returned.
    assert storage.record(record["run_id"]) == finished


def test_a_partial_artefact_is_in_storage_and_on_the_record_while_the_run_is_going():
    storage = DictStorage()
    at_step_one, release = threading.Event(), threading.Event()

    def on_step(index):
        if index == 1:
            at_step_one.set()
            assert release.wait(10)

    provider = FakeSimulationProvider(on_step=on_step)
    record = _record(provider, {"steps": 2})
    thread = threading.Thread(target=execute_run, args=(provider, record, storage), kwargs={"scope": "shared"})
    thread.start()
    try:
        assert at_step_one.wait(10)
        live = storage.record(record["run_id"])
        assert live["status"] == "running" and live["finished_at"] is None
        (timeline,) = live["artefacts"]
        assert timeline["partial"] is True and timeline["revision"] == 1
        assert json.loads(storage.get_bytes(timeline["key"])) == {"steps": [{"step": 1}]}
    finally:
        release.set()
        thread.join(10)
    assert storage.record(record["run_id"])["status"] == "done"


def test_a_failing_provider_ends_the_run_as_an_error_with_its_log():
    storage = DictStorage()
    provider = FakeSimulationProvider()
    finished = execute_run(provider, _record(provider, {"steps": 3, "fail_at": 2}), storage, scope="shared")
    assert finished["status"] == "error"
    assert finished["error"] == "RuntimeError: step 2 failed on request"
    by_name = {a["name"]: a for a in finished["artefacts"]}
    # What was stored before the failure is still listed, and still marked as unfinished.
    assert by_name["timeline.json"]["partial"] is True
    assert "step 2 failed on request" in storage.get_bytes(by_name["log.txt"]["key"]).decode()


def test_cancelling_through_the_event_stops_the_run_between_steps():
    storage = DictStorage()
    cancel = threading.Event()
    provider = FakeSimulationProvider(on_step=lambda index: cancel.set() if index == 2 else None)
    finished = execute_run(provider, _record(provider, {"steps": 50}), storage, scope="shared", cancel_event=cancel)
    assert finished["status"] == "cancelled" and finished["error"] is None
    assert json.loads(storage.get_bytes(finished["artefacts"][0]["key"])) == {"steps": [{"step": 1}, {"step": 2}]}


def test_a_cancel_marker_in_storage_stops_a_run_in_another_process(monkeypatch):
    monkeypatch.setattr("ada.simulation.runner._CANCEL_POLL_S", 0.0)
    storage = DictStorage()
    provider = FakeSimulationProvider()
    record = _record(provider, {"steps": 50})
    provider.on_step = lambda index: storage.put_bytes(cancel_key(record["run_id"]), b"x") if index == 3 else None
    finished = execute_run(provider, record, storage, scope="shared")
    assert finished["status"] == "cancelled"
    assert finished["progress"] == pytest.approx(3 / 50)


def test_a_run_cancelled_before_it_started_never_calls_the_provider():
    storage = DictStorage()

    class MustNotRun(FakeSimulationProvider):
        def run(self, case, ctx):
            raise AssertionError("the provider was called")

    provider = MustNotRun()
    record = _record(provider)
    storage.put_bytes(cancel_key(record["run_id"]), b"x")
    finished = execute_run(provider, record, storage, scope="shared")
    assert finished["status"] == "cancelled" and finished["started_at"] is None


# ── the domain's artefact roles ──────────────────────────────────────


@pytest.mark.parametrize("domain,names", [("fea", {"result.json"}), ("cfd", {"mesh.json", "fields.json"})])
def test_each_domain_ends_with_its_required_roles(domain, names):
    storage = DictStorage()
    provider = FakeSimulationProvider(domain=domain)
    finished = execute_run(provider, _record(provider), storage, scope="shared")
    assert finished["status"] == "done"
    assert {a["name"] for a in finished["artefacts"]} == names | {"log.txt"}


def test_a_finished_run_missing_a_required_role_is_an_error():
    storage = DictStorage()
    provider = FakeSimulationProvider()  # robotics: needs a model and a timeline
    finished = execute_run(provider, _record(provider, {"steps": 1, "skip_role": "model"}), storage, scope="shared")
    assert finished["status"] == "error"
    assert "a finished robotics run needs a final artefact for each of the roles model" in finished["error"]


def test_a_role_that_is_not_the_domains_is_refused_when_stored():
    class WrongRole(FakeSimulationProvider):
        def run(self, case, ctx):
            ctx.store_artefact("fields.bin", b"\0", role="fields")  # a cfd role, in a robotics run
            return SimulationResult()

    storage = DictStorage()
    provider = WrongRole()
    finished = execute_run(provider, _record(provider), storage, scope="shared")
    assert finished["status"] == "error"
    assert "'fields' is not a role of a robotics run" in finished["error"]
    assert not any(k.endswith("fields.bin") for k in storage.blobs)


# ── the context's edges ──────────────────────────────────────────────


def test_a_provider_reads_only_its_own_input_files_and_can_store_a_file_from_disk():
    seen = {}

    class Reader(FakeSimulationProvider):
        def run(self, case, ctx):
            local = ctx.fetch_input("in/deck.inp")
            seen["fetched"] = local.read_bytes()
            seen["scope"] = case.scope
            with pytest.raises(ValueError):
                ctx.read_input("someone/elses.key")
            out = ctx.workdir / "out.rmed"
            out.write_bytes(b"result bytes")
            seen["workdir"] = ctx.workdir
            return SimulationResult(artefacts=(ctx.store_artefact("out.rmed", out, role="result"),))

    storage = DictStorage()
    storage.put_bytes("in/deck.inp", b"deck")
    provider = Reader(domain="fea")
    finished = execute_run(provider, _record(provider, inputs=["in/deck.inp"]), storage, scope="projects/p1")
    assert finished["status"] == "done", finished["error"]
    assert seen["fetched"] == b"deck" and seen["scope"] == "projects/p1"
    (artefact,) = finished["artefacts"]
    assert artefact["size"] == len(b"result bytes") and storage.get_bytes(artefact["key"]) == b"result bytes"
    assert not seen["workdir"].exists()  # the scratch directory goes with the run


def test_a_result_may_only_list_artefacts_stored_through_the_context():
    from ada.simulation import SimulationArtefact

    class Forger(FakeSimulationProvider):
        def run(self, case, ctx):
            return SimulationResult(artefacts=(SimulationArtefact("x.glb", "somewhere/else.glb", role="model"),))

    provider = Forger()
    finished = execute_run(provider, _record(provider), DictStorage(), scope="shared")
    assert finished["status"] == "error" and "was not stored through the context" in finished["error"]


def test_raise_if_cancelled_raises_the_cancel_exception():
    raised = {}

    class Checker(FakeSimulationProvider):
        def run(self, case, ctx):
            cancel.set()
            try:
                ctx.raise_if_cancelled()
            except SimulationCancelled:
                raised["yes"] = True
                raise

    cancel = threading.Event()
    provider = Checker()
    finished = execute_run(provider, _record(provider), DictStorage(), scope="shared", cancel_event=cancel)
    assert raised and finished["status"] == "cancelled"
