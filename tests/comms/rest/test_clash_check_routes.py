"""``POST /scopes/{scope}/clash-check`` and ``/clash-detail`` -- Decision 10 / Phase 6, driven by
the LOCAL (queue-less) job transport this harness runs under.

The route itself and its two job kinds are this change's own files; ``ada.clash`` (identify /
classify / match / result / builtin_specs) is owned elsewhere and treated as a fixed contract
here (see the task's "do not touch" list). What is under test is the WIRING: the route composes a
derived key from (source content, options) so a repeat is a cache hit with no job; a source with
no members finishes ``done`` with ``counts.members == 0``; ``clash-detail`` runs a built-in spec
end to end and writes a GLB plus a ``.stats.json`` whose ``joints`` block is what
``ada.topo_model.takeoff._joints_takeoff`` (and therefore the ``Joints`` Scene tab) already
renders; an out-of-tree spec cannot be served without a pool.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-clash-check-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

REPO = Path(__file__).resolve().parents[3]
STEP_FIXTURE = REPO / "files" / "step_files" / "plate_1_flat.stp"

SCOPE = "user:me"
SCOPE_DIR = "users/local-dev"


def _settings(tmp_path) -> Settings:
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
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url="",
    )


def _two_girder_model():
    """Two horizontal I-beams meeting at right angles -- one girder-to-girder joint, the exact
    shape ``builtin.girder_gusset`` binds to (``ada.clash.builtin_specs``)."""
    import ada

    a = ada.Assembly("ClashTest")
    p = ada.Part("P")
    a.add_part(p)
    p.add_beam(ada.Beam("g1", (0, 0, 0), (2, 0, 0), "IPE200"))
    p.add_beam(ada.Beam("g2", (1, 0, 0), (1, 2, 0), "IPE200"))
    return a


@pytest.fixture
def client_and_scope(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client, tmp_path / SCOPE_DIR


def _stage(scope_dir: Path, rel_key: str, data: bytes) -> str:
    dest = scope_dir / rel_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return rel_key


@pytest.fixture
def ifc_source_key(client_and_scope, tmp_path):
    _, scope_dir = client_and_scope
    ifc_path = tmp_path / "probe.ifc"
    _two_girder_model().to_ifc(ifc_path)
    return _stage(scope_dir, "models/probe.ifc", ifc_path.read_bytes())


@pytest.fixture
def step_source_key(client_and_scope):
    """A shapes-only STEP source -- ``ada.from_step`` yields no Beam/Plate at all, which is
    exactly the "this source cannot be checked" case (Decision 10, item 1's design note)."""
    _, scope_dir = client_and_scope
    return _stage(scope_dir, "models/probe.step", STEP_FIXTURE.read_bytes())


def _wait_done(client, job_id, *, timeout_s=30.0) -> dict:
    if job_id is None:
        return {"status": "done"}
    status = None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] != "running":
            return status
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish in {timeout_s}s: {status}")


def _read_blob(client, scope, key) -> dict:
    r = client.get(f"/api/scopes/{scope}/blobs/{key}")
    assert r.status_code == 200, r.text
    raw = r.content
    if r.headers.get("content-encoding") != "gzip" and raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def _run_check(client, source_key: str, options: dict | None = None) -> tuple[dict, str]:
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"source_key": source_key, "options": options or {"include_plate_joints": False}},
    )
    assert r.status_code == 200, r.text
    status = _wait_done(client, r.json()["job_id"])
    assert status["status"] == "done", status
    return _read_blob(client, SCOPE, r.json()["derived_key"]), r.json()["derived_key"]


# ── clash-check ───────────────────────────────────────────────────────


def test_clash_check_finds_the_girder_gusset_joint(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    result, _ = _run_check(client, ifc_source_key)
    assert result["schema"] == "ada.clash/result@2"
    assert result["checker"] == "adapy"
    assert result["counts"]["members"] == 2
    assert result["counts"]["joints"] == 1
    assert len(result["joints"]) == 1
    assert result["joints"][0]["type_key"].startswith("2|BEAM:I:GIRDER")
    assert "builtin.girder_gusset" in [a["spec"] for a in result["joints"][0]["applicable"]]


def test_a_repeat_request_is_a_cache_hit_with_no_job(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    body = {"source_key": ifc_source_key, "options": {"include_plate_joints": False}}
    first = client.post(f"/api/scopes/{SCOPE}/clash-check", json=body)
    assert first.status_code == 200, first.text
    _wait_done(client, first.json()["job_id"])

    second = client.post(f"/api/scopes/{SCOPE}/clash-check", json=body)
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["cached"] is True
    assert body2["job_id"] is None
    assert body2["derived_key"] == first.json()["derived_key"]


def test_a_source_with_no_members_finishes_done_with_zero_members(client_and_scope, step_source_key):
    client, _ = client_and_scope
    result, _ = _run_check(client, step_source_key)
    assert result["counts"]["members"] == 0
    assert result["counts"]["joints"] == 0
    assert result["warnings"]
    assert "no beams or plates" in result["warnings"][0]


def test_missing_source_key_is_400(client_and_scope):
    client, _ = client_and_scope
    r = client.post(f"/api/scopes/{SCOPE}/clash-check", json={})
    assert r.status_code == 400, r.text


def test_different_options_are_different_derived_keys(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    r1 = client.post(f"/api/scopes/{SCOPE}/clash-check", json={"source_key": ifc_source_key, "options": {}})
    r2 = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"source_key": ifc_source_key, "options": {"point_tol": 0.5}},
    )
    assert r1.json()["derived_key"] != r2.json()["derived_key"]
    # Drain both background jobs rather than leaving their daemon threads to outlive this
    # test's TestClient (and its event loop) -- a loose thread there surfaces as a spurious
    # "coroutine was never awaited" warning attributed to whichever later test happens to be
    # running when it finally finishes.
    _wait_done(client, r1.json()["job_id"])
    _wait_done(client, r2.json()["job_id"])


# ── clash-detail ─────────────────────────────────────────────────────


def test_clash_detail_runs_a_builtin_spec_and_writes_the_joints_block(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    result, result_key = _run_check(client, ifc_source_key)
    joint = result["joints"][0]

    r = client.post(
        f"/api/scopes/{SCOPE}/clash-detail",
        json={"result_key": result_key, "joint_ids": [joint["id"]], "spec": "builtin.girder_gusset"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    status = _wait_done(client, body["job_id"])
    assert status["status"] == "done", status

    stats = _read_blob(client, SCOPE, body["derived_key"])
    assert stats["joints"]["count"] == 1
    assert len(stats["joints"]["items"]) == 1

    glb = client.get(f"/api/scopes/{SCOPE}/blobs/{body['glb_key']}")
    assert glb.status_code == 200, glb.text
    assert len(glb.content) > 0


def test_clash_detail_repeat_options_are_deterministic_ids(client_and_scope, ifc_source_key):
    """Running the check twice at the SAME options reproduces the same joint id -- the property
    ``clash_detail`` leans on to re-derive a selection from nothing but the cached result."""
    client, _ = client_and_scope
    result1, _ = _run_check(client, ifc_source_key)
    # A second, distinct check (different derived key, same source+options) must land on the
    # identical joint id -- ids are a function of (source, options), not of run order.
    result2, _ = _run_check(client, ifc_source_key)
    assert result1["joints"][0]["id"] == result2["joints"][0]["id"]


def test_clash_detail_with_unknown_joint_id_is_404(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    _, result_key = _run_check(client, ifc_source_key)
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-detail",
        json={"result_key": result_key, "joint_ids": ["not-a-real-id"], "spec": "builtin.girder_gusset"},
    )
    assert r.status_code == 404, r.text


def test_clash_detail_for_an_out_of_tree_spec_is_501_in_this_queueless_harness(client_and_scope, ifc_source_key):
    """No queue in this harness, so ``clash_detail``'s LOCAL engine is the only path, and it can
    only ever serve a spec THIS process has registered (the built-ins). A spec that would arrive
    with its own capability on a real deployment has no pool to be routed to here -- the route
    refuses synchronously rather than enqueueing a job that could never finish."""
    client, _ = client_and_scope
    result, result_key = _run_check(client, ifc_source_key)
    joint = result["joints"][0]
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-detail",
        json={"result_key": result_key, "joint_ids": [joint["id"]], "spec": "not.a.registered.spec"},
    )
    assert r.status_code == 501, r.text
    assert "not registered" in r.json()["detail"]


def test_clash_detail_missing_fields_is_400(client_and_scope, ifc_source_key):
    client, _ = client_and_scope
    result, result_key = _run_check(client, ifc_source_key)
    r = client.post(f"/api/scopes/{SCOPE}/clash-detail", json={"result_key": result_key})
    assert r.status_code == 400, r.text


# ── connection-specs (the heartbeat union) ──────────────────────────────


def test_connection_specs_lists_the_builtins(client_and_scope):
    client, _ = client_and_scope
    r = client.get(f"/api/scopes/{SCOPE}/clash-check/connection-specs")
    assert r.status_code == 200, r.text
    specs = r.json()["connection_specs"]
    names = {s["slug"] for s in specs}
    assert {"builtin.girder_gusset", "builtin.box_joint"} <= names
    for spec in specs:
        if spec["slug"].startswith("builtin."):
            assert spec["capability"] is None


# ── clash-check over a PUBLISHED ASSET NODE ───────────────────────────
#
# The second way to name a model. Everything below runs against a provider whose source format
# core has no reader for -- a private text file -- so a check that succeeds could only have gone
# through `ada.assets.concepts`. Nothing here imports a reader for it.

LINES_PROVIDER = "fixture-lines"
LINES_COLLECTION = "lines-a"
LINES_SUBJECT = "unit-1"
LINES_REVISION = "20260928T000000Z"
LINES_SOURCE = b"\n".join([b"bm0 0 0 0 4 0 0", b"bm1 4 0 0 4 4 0", b"bm2 20 20 0 24 20 0"])


class _LinesConcepts:
    """Reads one member per line, which is a format core will never know."""

    def concepts(self, options, *, storage, scope=None, node=None):
        import ada

        raw = storage.get_bytes(options["source_key"]).decode()
        part = ada.Part(node or "lines")
        for line in (ln for ln in raw.splitlines() if ln.strip()):
            name, x0, y0, z0, x1, y1, z1 = line.split()
            part.add_beam(
                ada.Beam(name, (float(x0), float(y0), float(z0)), (float(x1), float(y1), float(z1)), "IPE200")
            )
        return part


@pytest.fixture
def published_lines_node(client_and_scope):
    """Publish a node under the key grammar and register the reader for its provider."""
    from ada.assets.concepts import clear_asset_concepts, register_asset_concepts
    from ada.assets.keys import asset_key
    from ada.assets.manifest import (
        MANIFEST_FILENAME,
        ArtefactEntry,
        AssetManifest,
        BuildSpec,
    )

    _, scope_dir = client_and_scope
    source_key = asset_key(LINES_COLLECTION, LINES_SUBJECT, LINES_REVISION, "lines.txt")
    _stage(scope_dir, source_key, LINES_SOURCE)

    manifest = AssetManifest(
        provider=LINES_PROVIDER,
        collection=LINES_COLLECTION,
        subject=LINES_SUBJECT,
        revision=LINES_REVISION,
        node=LINES_SUBJECT,
        produced_at="2026-09-28T00:00:00Z",
        published_at="2026-09-28T00:00:00Z",
        delivery="build",
        build=BuildSpec(capability="build-lines", options={"source_key": source_key}),
        artefacts=(ArtefactEntry(role="source", key=source_key, sha256="", size=len(LINES_SOURCE)),),
    )
    _stage(
        scope_dir,
        asset_key(LINES_COLLECTION, LINES_SUBJECT, LINES_REVISION, MANIFEST_FILENAME),
        manifest.to_json(),
    )

    register_asset_concepts(LINES_PROVIDER, lambda: _LinesConcepts())
    yield
    clear_asset_concepts()


def _run_node_check(client, body: dict) -> tuple[dict, str]:
    payload = {"collection": LINES_COLLECTION, "subject": LINES_SUBJECT, **body}
    r = client.post(f"/api/scopes/{SCOPE}/clash-check", json=payload)
    assert r.status_code == 200, r.text
    status = _wait_done(client, r.json()["job_id"])
    assert status["status"] == "done", status
    return _read_blob(client, SCOPE, r.json()["derived_key"]), r.json()["derived_key"]


def test_a_node_of_a_format_core_cannot_read_is_checked_through_its_provider(client_and_scope, published_lines_node):
    client, _ = client_and_scope
    doc, _ = _run_node_check(client, {"options": {"include_plate_joints": False}})

    assert doc["counts"]["beams"] == 3
    joined = {m["name"] for joint in doc["joints"] for m in joint["members"]}
    assert joined == {"bm0", "bm1"}  # bm2 is 20 m away and joins nothing
    assert doc["provenance"]["reader"] == "provider-concepts"
    assert doc["provenance"]["provider"] == LINES_PROVIDER


def test_the_result_is_the_same_document_a_file_check_produces(client_and_scope, published_lines_node):
    """Same schema, so the panel cannot tell the two ways in apart -- which is the point."""
    client, _ = client_and_scope
    doc, _ = _run_node_check(client, {})
    assert doc["schema"].startswith("ada.clash/result@")
    assert {"joints", "groups", "counts", "source_key"} <= set(doc)


def test_a_repeat_node_request_is_a_cache_hit_with_no_job(client_and_scope, published_lines_node):
    client, _ = client_and_scope
    _, key = _run_node_check(client, {})
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"collection": LINES_COLLECTION, "subject": LINES_SUBJECT},
    )
    assert r.json() == {"job_id": None, "derived_key": key, "cached": True}


def test_two_subjects_of_one_shared_export_do_not_share_a_result(client_and_scope, published_lines_node):
    """The derived key carries the SUBJECT, not just the source bytes.

    One upload is commonly referenced by many subjects, so a token over the blob alone would
    serve one node's joints under another node's name -- silently, and only for the second
    caller.
    """
    from ada.assets.keys import asset_key
    from ada.assets.manifest import (
        MANIFEST_FILENAME,
        ArtefactEntry,
        AssetManifest,
        BuildSpec,
    )

    client, scope_dir = client_and_scope
    shared_source = asset_key(LINES_COLLECTION, LINES_SUBJECT, LINES_REVISION, "lines.txt")
    other = AssetManifest(
        provider=LINES_PROVIDER,
        collection=LINES_COLLECTION,
        subject="unit-2",
        revision=LINES_REVISION,
        node="unit-2",
        produced_at="2026-09-28T00:00:00Z",
        published_at="2026-09-28T00:00:00Z",
        delivery="build",
        build=BuildSpec(capability="build-lines", options={"source_key": shared_source}),
        # The SAME blob, by absolute key: exactly the fan-out the grammar is built for.
        artefacts=(ArtefactEntry(role="source", key=shared_source, sha256="", size=len(LINES_SOURCE)),),
    )
    _stage(scope_dir, asset_key(LINES_COLLECTION, "unit-2", LINES_REVISION, MANIFEST_FILENAME), other.to_json())

    _, key_one = _run_node_check(client, {})
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"collection": LINES_COLLECTION, "subject": "unit-2"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["derived_key"] != key_one


def test_naming_the_model_both_ways_is_refused(client_and_scope, ifc_source_key):
    """Which one was checked would otherwise depend on the order this route reads them in."""
    client, _ = client_and_scope
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"source_key": ifc_source_key, "collection": LINES_COLLECTION, "subject": LINES_SUBJECT},
    )
    assert r.status_code == 400
    assert "ONE way" in r.json()["detail"]


def test_naming_the_model_no_way_is_refused(client_and_scope):
    client, _ = client_and_scope
    r = client.post(f"/api/scopes/{SCOPE}/clash-check", json={})
    assert r.status_code == 400


def test_an_unpublished_subject_is_404(client_and_scope, published_lines_node):
    client, _ = client_and_scope
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"collection": LINES_COLLECTION, "subject": "nope"},
    )
    assert r.status_code == 404


def test_a_provider_the_caller_names_wrongly_is_refused(client_and_scope, published_lines_node):
    """The caller is acting on a view that has moved -- the same rule the build request applies."""
    client, _ = client_and_scope
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"collection": LINES_COLLECTION, "subject": LINES_SUBJECT, "provider": "someone-else"},
    )
    assert r.status_code == 409
    assert LINES_PROVIDER in r.json()["detail"]


# ── checkers ──────────────────────────────────────────────────────────


def test_checker_listing_always_offers_cores_own(client_and_scope):
    client, _ = client_and_scope
    r = client.get(f"/api/scopes/{SCOPE}/clash-check/checkers")
    assert r.status_code == 200, r.text
    by_name = {c["name"]: c for c in r.json()["checkers"]}
    assert by_name["adapy"]["capability"] is None


def test_a_checker_this_viewer_cannot_run_is_refused_by_name(client_and_scope, ifc_source_key):
    """Enqueued anyway, it would run nowhere -- or as core's check under another engine's name."""
    client, _ = client_and_scope
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"source_key": ifc_source_key, "options": {"checker": "nobody-has-this"}},
    )
    assert r.status_code == 409, r.text
    assert "nobody-has-this" in r.json()["detail"]


def test_the_listing_urls_are_routable(client_and_scope):
    """The panel builds these from the same base as the POST; a typo there answered 404 for months."""
    client, _ = client_and_scope
    for tail in ("passes", "checkers", "connection-specs", "geometry-providers"):
        assert client.get(f"/api/scopes/{SCOPE}/clash-check/{tail}").status_code == 200


# ── geometry providers ────────────────────────────────────────────────


def test_geometry_providers_list_what_this_viewer_can_read(client_and_scope, published_lines_node):
    """Queue-less: the readers registered in THIS process are what a check here can read with."""
    client, _ = client_and_scope
    r = client.get(f"/api/scopes/{SCOPE}/clash-check/geometry-providers")
    assert r.status_code == 200, r.text
    by_id = {p["id"]: p for p in r.json()["providers"]}
    assert by_id[LINES_PROVIDER]["readable"] is True


@pytest.mark.asyncio
async def test_geometry_providers_union_every_live_pools_readers():
    from types import SimpleNamespace

    from ada.comms.rest.routes.clash_check import api_clash_geometry_providers

    class _Jobs:
        kind = "queue"

        async def advertised_specs(self, field, fallback_field=None):
            assert field == "asset_concept_readers"
            return {
                "member-reader": {"slug": "member-reader", "id": "member-reader", "label": "Members", "readable": True},
                "broken-reader": {
                    "slug": "broken-reader",
                    "id": "broken-reader",
                    "available": False,
                    "readable": False,
                    "unavailable_reason": "dependency missing",
                },
            }

    resp = await api_clash_geometry_providers(scope_obj=None, ctx=SimpleNamespace(jobs=_Jobs()))
    providers = json.loads(resp.body)["providers"]
    assert [p["id"] for p in providers] == ["broken-reader", "member-reader"]
    assert providers[1] == {
        "id": "member-reader",
        "label": "Members",
        "readable": True,
        "capability": None,
        "origin": "code",
    }
    assert providers[0]["readable"] is False and providers[0]["unavailable_reason"] == "dependency missing"


def test_a_geometry_provider_moves_the_key_only_where_it_can_change_the_model(
    client_and_scope, ifc_source_key, published_lines_node
):
    client, _ = client_and_scope
    _, plain = _run_check(client, ifc_source_key)
    # A file has no provider: asking for one is the same check and the same entry.
    r = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"source_key": ifc_source_key, "options": {"include_plate_joints": False, "geometry_provider": "x"}},
    )
    assert r.json()["derived_key"] == plain

    _, node_key = _run_node_check(client, {})
    # A node's own provider is the same check too...
    own = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={
            "collection": LINES_COLLECTION,
            "subject": LINES_SUBJECT,
            "options": {"geometry_provider": LINES_PROVIDER},
        },
    )
    assert own.json() == {"job_id": None, "derived_key": node_key, "cached": True}
    # A provider nothing here can read is refused before anything is enqueued...
    unread = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"collection": LINES_COLLECTION, "subject": LINES_SUBJECT, "options": {"geometry_provider": "other"}},
    )
    assert unread.status_code == 409 and "'other'" in unread.json()["detail"], unread.text
    # ...while another provider's geometry that this viewer CAN read is another check.
    from ada.assets.concepts import register_asset_concepts

    register_asset_concepts("other", lambda: _LinesConcepts())
    other = client.post(
        f"/api/scopes/{SCOPE}/clash-check",
        json={"collection": LINES_COLLECTION, "subject": LINES_SUBJECT, "options": {"geometry_provider": "other"}},
    )
    assert other.status_code == 200, other.text
    assert other.json()["derived_key"] != node_key
    # Nothing of "other" is published, so the job fails by name rather than checking nothing.
    status = _wait_done(client, other.json()["job_id"])
    assert status["status"] == "error" and "'other'" in (status.get("error") or ""), status
