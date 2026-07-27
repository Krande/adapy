"""Equipment-type & system-template catalogs: API CRUD + validation.

* No-DB path (always runs): endpoints 503 without a database; the doc
  validators + key conventions work standalone.
* Live-Postgres tests (skipped unless ``ADA_TEST_POSTGRES_URL`` is set):
  migrations 023/024 apply, CRUD round-trips, slug + revision conflicts,
  CAD copy-from-scope, cross-scope isolation, dropdown union.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

import ada.comms.rest.db as dbm  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.catalog import (  # noqa: E402
    equipment_cad_key,
    equipment_preview_glb_key,
    slugify,
    validate_equipment_doc,
    validate_system_doc,
)
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.converter import is_hidden_key  # noqa: E402

POSTGRES_URL = os.environ.get("ADA_TEST_POSTGRES_URL", "").strip()
needs_postgres = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ADA_TEST_POSTGRES_URL not set; skipping live Postgres tests",
)


def _settings(tmp_path: pathlib.Path, database_url: str = "") -> Settings:
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
        database_url=database_url,
    )


# ── standalone helpers ───────────────────────────────────────────────


def test_key_conventions_are_hidden():
    assert equipment_cad_key("abc", ".STEP") == "_equipment/abc/source.step"
    assert equipment_preview_glb_key("abc") == "_equipment/abc/preview.glb"
    assert is_hidden_key(equipment_cad_key("abc", ".step"))
    assert is_hidden_key(equipment_preview_glb_key("abc"))


def test_slugify():
    assert slugify("Big Pump #3 (v2)") == "big-pump-3-v2"
    assert slugify("  Centrifugal  Pump  ") == "centrifugal-pump"
    assert slugify("!!!") == ""


def test_validate_equipment_doc_normalizes_and_rejects():
    out = validate_equipment_doc(
        {
            "bbox": {"lx": 2, "ly": 1, "lz": 3},
            "mass": 500,
            "ifc_element_class": "IfcPump",
            "ports": [{"name": "suction", "position": [-1, 0, 0.5], "direction_vector": [-1, 0, 0], "direction": "IN"}],
        }
    )
    assert out["bbox"] == {"lx": 2.0, "ly": 1.0, "lz": 3.0}
    assert out["ports"][0]["category"] == "process"  # defaulted
    with pytest.raises(ValueError):
        validate_equipment_doc({"ports": [{"name": "a"}, {"name": "a"}]})  # dup port
    with pytest.raises(ValueError):
        validate_equipment_doc({"ports": [{"name": "x", "direction": "SIDEWAYS"}]})  # bad direction


def test_validate_system_doc():
    out = validate_system_doc({"type": "electrical", "voltage": 690})
    assert out["type"] == "electrical" and out["voltage"] == 690
    with pytest.raises(ValueError):
        validate_system_doc({"type": "telepathy"})


# ── no-DB API path ───────────────────────────────────────────────────


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


def test_endpoints_503_without_db(app_client: TestClient):
    assert app_client.get("/api/scopes/shared/equipment-types").status_code == 503
    assert app_client.post("/api/scopes/shared/equipment-types", json={"name": "Pump"}).status_code == 503
    assert app_client.get("/api/scopes/shared/system-templates").status_code == 503


# ── live-Postgres API path ───────────────────────────────────────────


@pytest.fixture
def pg_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path, database_url=POSTGRES_URL))
    with TestClient(app) as client:
        yield client


@needs_postgres
def test_equipment_crud_and_conflicts(pg_client: TestClient):
    r = pg_client.post("/api/scopes/shared/equipment-types", json={"name": "Big Pump"})
    assert r.status_code == 201, r.text
    et = r.json()
    tid = et["id"]
    assert et["slug"] == "big-pump" and et["revision"] == 0
    try:
        # duplicate slug -> 409
        assert pg_client.post("/api/scopes/shared/equipment-types", json={"name": "Big Pump"}).status_code == 409
        # listed + feeds the cellbuilder dropdown
        assert any(
            e["id"] == tid for e in pg_client.get("/api/scopes/shared/equipment-types").json()["equipment_types"]
        )
        # dropdown returns origin-tagged objects; a DB entry shows origin=catalog
        dropdown = pg_client.get("/api/scopes/shared/procedural-models/equipment-types").json()["equipment_types"]
        entry = next((e for e in dropdown if e["slug"] == "big-pump"), None)
        assert entry is not None and entry["origin"] == "catalog" and entry["id"] == tid
        # syncing a code archetype needs a live worker advertising it -> 404 here
        assert (
            pg_client.post(
                "/api/scopes/shared/procedural-models/equipment-types/sync", json={"slug": "pump"}
            ).status_code
            == 404
        )

        # commit a doc (bbox/ports)
        doc = {
            "bbox": {"lx": 1, "ly": 1, "lz": 2},
            "mass": 750,
            "ifc_element_class": "IfcPump",
            "ports": [{"name": "discharge", "position": [0, 0, 2], "direction_vector": [0, 0, 1], "direction": "OUT"}],
        }
        r = pg_client.put(
            f"/api/scopes/shared/equipment-types/{tid}", json={"name": "Big Pump", "doc": doc, "base_revision": 0}
        )
        assert r.status_code == 200 and r.json()["revision"] == 1

        # stale base_revision -> 409
        r = pg_client.put(
            f"/api/scopes/shared/equipment-types/{tid}", json={"name": "Big Pump", "doc": doc, "base_revision": 0}
        )
        assert r.status_code == 409

        # invalid doc -> 422 (duplicate port names)
        bad = {"ports": [{"name": "a"}, {"name": "a"}]}
        r = pg_client.put(
            f"/api/scopes/shared/equipment-types/{tid}", json={"name": "Big Pump", "doc": bad, "base_revision": 1}
        )
        assert r.status_code == 422

        # fetch carries the doc
        got = pg_client.get(f"/api/scopes/shared/equipment-types/{tid}").json()
        assert got["revision"] == 1 and got["doc"]["ports"][0]["name"] == "discharge"

        # cross-scope isolation
        assert pg_client.get(f"/api/scopes/user:me/equipment-types/{tid}").status_code == 404

        # infer-bbox with no CAD -> 400
        assert pg_client.post(f"/api/scopes/shared/equipment-types/{tid}/infer-bbox").status_code == 400

        # copy-from-scope needs an existing source
        assert (
            pg_client.post(
                f"/api/scopes/shared/equipment-types/{tid}/cad-from-scope", json={"source_key": "nope.step"}
            ).status_code
            == 404
        )
    finally:
        assert pg_client.delete(f"/api/scopes/shared/equipment-types/{tid}").status_code == 200
    assert pg_client.get(f"/api/scopes/shared/equipment-types/{tid}").status_code == 404


@needs_postgres
def test_system_template_crud(pg_client: TestClient):
    r = pg_client.post("/api/scopes/shared/system-templates", json={"name": "Cooling Water"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert r.json()["slug"] == "cooling-water"
    try:
        r = pg_client.put(
            f"/api/scopes/shared/system-templates/{sid}",
            json={"name": "Cooling Water", "doc": {"type": "piping", "medium": "water"}, "base_revision": 0},
        )
        assert r.status_code == 200 and r.json()["revision"] == 1
        got = pg_client.get(f"/api/scopes/shared/system-templates/{sid}").json()
        assert got["doc"]["medium"] == "water"
        # invalid type -> 422
        r = pg_client.put(
            f"/api/scopes/shared/system-templates/{sid}",
            json={"name": "Cooling Water", "doc": {"type": "telepathy"}, "base_revision": 1},
        )
        assert r.status_code == 422
        # system-types dropdown unions the static built-in kinds (origin code)
        # with the DB template (origin catalog) — no worker required
        dropdown = pg_client.get("/api/scopes/shared/procedural-models/system-types").json()["system_types"]
        by_slug = {e["slug"]: e for e in dropdown}
        assert by_slug["cooling-water"]["origin"] == "catalog" and by_slug["cooling-water"]["type"] == "piping"
        assert by_slug["duct"]["origin"] == "code" and by_slug["electrical"]["voltage"] == 400
        # sync a built-in kind into the DB catalog (static -> works without a worker)
        r = pg_client.post("/api/scopes/shared/procedural-models/system-types/sync", json={"slug": "duct"})
        assert r.status_code == 201, r.text
        synced_id = r.json()["id"]
        assert pg_client.get(f"/api/scopes/shared/system-templates/{synced_id}").json()["doc"]["type"] == "duct"
        assert pg_client.delete(f"/api/scopes/shared/system-templates/{synced_id}").status_code == 200
        # unknown kind -> 404
        assert (
            pg_client.post(
                "/api/scopes/shared/procedural-models/system-types/sync", json={"slug": "telepathy"}
            ).status_code
            == 404
        )
    finally:
        assert pg_client.delete(f"/api/scopes/shared/system-templates/{sid}").status_code == 200


@needs_postgres
def test_cad_copy_from_scope_and_bbox_map(pg_client: TestClient, tmp_path: pathlib.Path):
    r = pg_client.post("/api/scopes/shared/equipment-types", json={"name": "Copy Pump"})
    tid = r.json()["id"]
    try:
        # seed a scope source file in the app's local storage root
        src = tmp_path / "shared" / "cad" / "pump.step"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"ISO-10303-21;\n")
        r = pg_client.post(
            f"/api/scopes/shared/equipment-types/{tid}/cad-from-scope", json={"source_key": "cad/pump.step"}
        )
        assert r.status_code == 201, r.text
        assert r.json()["cad_key"] == equipment_cad_key(tid, ".step")
        got = pg_client.get(f"/api/scopes/shared/equipment-types/{tid}").json()
        assert got["cad_key"] == equipment_cad_key(tid, ".step")
    finally:
        pg_client.delete(f"/api/scopes/shared/equipment-types/{tid}")


@needs_postgres
@pytest.mark.asyncio
async def test_db_helpers_direct():
    pool = await dbm.init_pool(POSTGRES_URL)
    assert pool is not None
    try:
        row = await dbm.create_equipment_type(
            pool, scope_kind="user", scope_id="sub-a", slug="pump", name="Pump", description=None, created_by="sub-a"
        )
        assert row is not None and row["revision"] == 0
        tid = row["id"]
        # same slug other scope is fine
        row2 = await dbm.create_equipment_type(
            pool, scope_kind="user", scope_id="sub-b", slug="pump", name="Pump", description=None, created_by="sub-b"
        )
        assert row2 is not None

        rev = await dbm.update_equipment_type(
            pool,
            tid,
            slug="pump",
            name="Pump",
            description="d",
            doc={"bbox": {"lx": 3, "ly": 3, "lz": 3}},
            base_revision=0,
        )
        assert rev == 1
        # inferred bbox merges without bumping the revision
        assert await dbm.apply_inferred_bbox(pool, tid, {"lx": 9, "ly": 8, "lz": 7}) is True
        got = await dbm.get_equipment_type(pool, tid)
        assert got["revision"] == 1 and got["doc"]["bbox"] == {"lx": 9, "ly": 8, "lz": 7}
        # scope-scoped slug->doc map for the compiler
        cmap = await dbm.get_equipment_docs_by_scope(pool, scope_kind="user", scope_id="sub-a")
        assert cmap["pump"]["bbox"] == {"lx": 9, "ly": 8, "lz": 7}

        assert await dbm.archive_equipment_type(pool, tid) is True
        assert await dbm.archive_equipment_type(pool, tid) is False
        await dbm.archive_equipment_type(pool, row2["id"])
    finally:
        await pool.close()
