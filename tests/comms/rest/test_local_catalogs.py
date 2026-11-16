"""The equipment-type and system-template catalogs of a local viewer (no database, no
queue): storage files under ``_catalogs/`` (``ada.comms.rest.catalog_store``), served by
the same routes and response shapes as the postgres catalogs, seeded with defaults."""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.catalog_store import (  # noqa: E402
    BOX_SLUG,
    DbCatalogStore,
    FileCatalogStore,
    catalog_store,
    default_equipment_types,
    default_system_templates,
    equipment_type_key,
)
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.converter import is_hidden_key  # noqa: E402

EQ = "/api/scopes/shared/equipment-types"
SY = "/api/scopes/shared/system-templates"
PM = "/api/scopes/shared/procedural-models"


def _settings(tmp_path: pathlib.Path) -> Settings:
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
        database_url="",
    )


@pytest.fixture
def client(tmp_path: pathlib.Path):
    with TestClient(create_app(_settings(tmp_path))) as c:
        yield c


def _by_slug(rows: list[dict]) -> dict[str, dict]:
    return {r["slug"]: r for r in rows}


def test_the_store_is_files_only_without_a_pool_and_a_queue():
    pool = object()
    assert isinstance(catalog_store(pool, object(), local=True), DbCatalogStore)
    assert isinstance(catalog_store(None, object(), local=True), FileCatalogStore)
    assert catalog_store(None, object(), local=False) is None


def test_the_defaults_are_a_box_and_the_code_archetypes():
    from ada.topo_model.equipment import EQUIPMENT_ARCHETYPES

    eq = _by_slug(default_equipment_types())
    assert set(eq) == {BOX_SLUG, *EQUIPMENT_ARCHETYPES}
    box = eq[BOX_SLUG]["doc"]
    assert box["ports"] == [] and box["mass"] > 0 and box["ifc_element_class"] == "IfcBuildingElementProxy"
    assert eq["pump"]["doc"]["ifc_element_class"] == "IfcPump" and eq["pump"]["doc"]["ports"]
    assert set(_by_slug(default_system_templates())) == {"piping", "duct", "cable", "electrical"}


def test_listing_seeds_the_defaults_once(client: TestClient, tmp_path: pathlib.Path):
    types = client.get(EQ).json()["equipment_types"]
    assert set(_by_slug(types)) == {d["slug"] for d in default_equipment_types()}
    assert [t["name"] for t in types] == sorted(t["name"] for t in types)
    first = types[0]
    assert set(first) >= {"id", "slug", "name", "description", "cad_key", "revision", "preview_glb_key"}
    assert first["revision"] == 0 and first["cad_key"] is None and "doc" not in first
    stored = tmp_path / "shared" / pathlib.PurePosixPath(equipment_type_key(first["id"]))
    assert stored.is_file() and is_hidden_key(equipment_type_key(first["id"]))
    # Seeded once: a second listing does not add another set.
    assert len(client.get(EQ).json()["equipment_types"]) == len(types)
    # Archiving everything leaves an empty catalog; it is not seeded again.
    for t in types:
        assert client.delete(f"{EQ}/{t['id']}").status_code == 200
    assert client.get(EQ).json() == {"equipment_types": []}
    assert stored.is_file()  # archived, not deleted


def test_equipment_crud_round_trip(client: TestClient):
    r = client.post(EQ, json={"name": "Big Pump"})
    assert r.status_code == 201, r.text
    et = r.json()
    tid = et["id"]
    assert et["slug"] == "big-pump" and et["revision"] == 0 and et["doc"]["bbox"] == {"lx": 1.0, "ly": 1.0, "lz": 1.0}
    assert client.post(EQ, json={"name": "Big Pump"}).status_code == 409
    assert tid in {t["id"] for t in client.get(EQ).json()["equipment_types"]}

    doc = {
        "bbox": {"lx": 1, "ly": 1, "lz": 2},
        "mass": 750,
        "ifc_element_class": "IfcPump",
        "ports": [{"name": "discharge", "position": [0, 0, 2], "direction_vector": [0, 0, 1], "direction": "OUT"}],
    }
    r = client.put(f"{EQ}/{tid}", json={"name": "Big Pump", "doc": doc, "base_revision": 0})
    assert r.status_code == 200 and r.json() == {"id": tid, "revision": 1}
    r = client.put(f"{EQ}/{tid}", json={"name": "Big Pump", "doc": doc, "base_revision": 0})
    assert r.status_code == 409 and r.json()["detail"]["current_revision"] == 1
    bad = {"ports": [{"name": "a"}, {"name": "a"}]}
    assert client.put(f"{EQ}/{tid}", json={"name": "Big Pump", "doc": bad, "base_revision": 1}).status_code == 422
    # A slug another live type has is a conflict, not an overwrite.
    r = client.put(f"{EQ}/{tid}", json={"name": "Pump", "slug": "pump", "doc": doc, "base_revision": 1})
    assert r.status_code == 409

    got = client.get(f"{EQ}/{tid}").json()
    assert got["revision"] == 1 and got["doc"]["ports"][0]["name"] == "discharge" and got["doc"]["mass"] == 750
    assert got["preview_glb_key"] is None

    # The Builder's dropdown shows the catalog entry, with its ports.
    entry = _by_slug(client.get(f"{PM}/equipment-types").json()["equipment_types"])["big-pump"]
    assert entry["origin"] == "catalog" and entry["id"] == tid
    assert entry["ports"][0]["position"] == [0, 0, 2]

    assert client.get(f"/api/scopes/user:me/equipment-types/{tid}").status_code == 404
    assert client.get(f"{EQ}/not-a-uuid").status_code == 404
    assert client.post(f"{EQ}/{tid}/infer-bbox").status_code == 400  # no CAD linked
    assert client.post(f"{EQ}/{tid}/cad-from-scope", json={"source_key": "nope.step"}).status_code == 404

    assert client.delete(f"{EQ}/{tid}").status_code == 200
    assert client.get(f"{EQ}/{tid}").status_code == 404
    assert client.delete(f"{EQ}/{tid}").status_code == 404
    # Archived: its slug is free again.
    assert client.post(EQ, json={"name": "Big Pump"}).status_code == 201


def test_cad_link_and_in_process_bbox_inference(client: TestClient, tmp_path: pathlib.Path):
    import trimesh

    tid = client.post(EQ, json={"name": "Meshed"}).json()["id"]
    src = tmp_path / "shared" / "cad" / "block.stl"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(trimesh.creation.box(extents=(1.0, 2.0, 3.0)).export(file_type="stl"))
    r = client.post(f"{EQ}/{tid}/cad-from-scope", json={"source_key": "cad/block.stl"})
    assert r.status_code == 201, r.text
    cad_key = r.json()["cad_key"]
    assert client.get(f"{EQ}/{tid}").json()["cad_key"] == cad_key

    # No worker here: the route infers in-process and answers when it is done.
    r = client.post(f"{EQ}/{tid}/infer-bbox")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["job_id"] is None and body["status"] == "done"
    got = client.get(f"{EQ}/{tid}").json()
    assert got["doc"]["bbox"] == pytest.approx({"lx": 1.0, "ly": 2.0, "lz": 3.0})
    assert got["revision"] == 0  # inference does not race the user's commits
    assert got["preview_glb_key"] == body["derived_key"]
    has_cad = _by_slug(client.get(f"{PM}/equipment-types").json()["equipment_types"])["meshed"]["has_cad"]
    assert has_cad is True

    r = client.post(f"{EQ}/{tid}/cad?filename=block.stl", content=src.read_bytes())
    assert r.status_code == 201 and r.json()["cad_key"] == cad_key


def test_system_template_crud_and_sync(client: TestClient):
    seeded = _by_slug(client.get(SY).json()["system_templates"])
    assert set(seeded) == {"piping", "duct", "cable", "electrical"}
    assert client.get(f"{SY}/{seeded['electrical']['id']}").json()["doc"]["voltage"] == 400

    sid = client.post(SY, json={"name": "Cooling Water"}).json()["id"]
    r = client.put(
        f"{SY}/{sid}", json={"name": "Cooling Water", "doc": {"type": "piping", "medium": "water"}, "base_revision": 0}
    )
    assert r.status_code == 200 and r.json()["revision"] == 1
    assert client.get(f"{SY}/{sid}").json()["doc"]["medium"] == "water"
    bad = {"name": "Cooling Water", "doc": {"type": "telepathy"}, "base_revision": 1}
    assert client.put(f"{SY}/{sid}", json=bad).status_code == 422
    dropdown = _by_slug(client.get(f"{PM}/system-types").json()["system_types"])
    assert dropdown["cooling-water"]["origin"] == "catalog" and dropdown["cooling-water"]["medium"] == "water"
    # A seeded kind keeps its own type and voltage, not the listing default.
    assert dropdown["duct"]["type"] == "duct" and dropdown["electrical"]["voltage"] == 400

    # Syncing a built-in kind: already there (seeded) -> 409; archived -> synced again.
    assert client.post(f"{PM}/system-types/sync", json={"slug": "duct"}).status_code == 409
    assert client.delete(f"{SY}/{seeded['duct']['id']}").status_code == 200
    r = client.post(f"{PM}/system-types/sync", json={"slug": "duct"})
    assert r.status_code == 201, r.text
    assert client.get(f"{SY}/{r.json()['id']}").json()["doc"]["type"] == "duct"
    assert client.post(f"{PM}/system-types/sync", json={"slug": "telepathy"}).status_code == 404

    assert client.delete(f"{SY}/{sid}").status_code == 200
    assert client.get(f"{SY}/{sid}").status_code == 404


def test_the_builder_catalogs_are_served_locally(client: TestClient):
    """A local viewer is the worker the procedural catalogs describe: it advertises
    the equipment archetypes, system kinds, cell/opening types and rulesets itself."""
    eq = _by_slug(client.get(f"{PM}/equipment-types").json()["equipment_types"])
    assert {BOX_SLUG, "pump", "tank", "switchboard"} <= set(eq)
    assert eq[BOX_SLUG]["ports"] == []
    assert {p["name"] for p in eq["pump"]["ports"]} >= {"suction", "discharge"}
    assert {"piping", "electrical"} <= set(_by_slug(client.get(f"{PM}/system-types").json()["system_types"]))
    assert "room" in _by_slug(client.get(f"{PM}/cell-types").json()["cell_types"])
    assert "door" in _by_slug(client.get(f"{PM}/opening-types").json()["opening_types"])


def test_equipment_sync_and_resync_locally(client: TestClient):
    types = _by_slug(client.get(EQ).json()["equipment_types"])
    # Seeded from the same archetype docs: a resync changes nothing.
    r = client.post(f"{PM}/equipment-types/resync")
    assert r.status_code == 200, r.text
    assert r.json()["created"] == [] and r.json()["updated"] == []
    assert "pump" in r.json()["unchanged"]
    # Archive one and sync it back from code.
    assert client.post(f"{PM}/equipment-types/sync", json={"slug": "tank"}).status_code == 409
    client.delete(f"{EQ}/{types['tank']['id']}")
    r = client.post(f"{PM}/equipment-types/sync", json={"slug": "tank"})
    assert r.status_code == 201, r.text
    assert client.get(f"{EQ}/{r.json()['id']}").json()["doc"]["ifc_element_class"]
    assert client.post(f"{PM}/equipment-types/sync", json={"slug": "nope"}).status_code == 404


@pytest.mark.asyncio
async def test_the_file_fingerprint_follows_edits(tmp_path: pathlib.Path):
    from ada.comms.rest.scope import Scope
    from ada.comms.rest.storage import Storage

    store = FileCatalogStore(Storage.from_settings(_settings(tmp_path)))
    scope = Scope.shared()
    # A build reading the catalog seeds it too, so the fingerprint is of the defaults.
    seeded = await store.fingerprint(scope)
    assert BOX_SLUG in await store.equipment_docs(scope)
    assert await store.fingerprint(scope) == seeded
    box = next(t for t in await store.list_equipment(scope) if t["slug"] == BOX_SLUG)
    doc = (await store.get_equipment(scope, box["id"]))["doc"]
    assert (
        await store.update_equipment(
            scope, box["id"], slug=BOX_SLUG, name="Box", description=None, doc={**doc, "mass": 5.0}, base_revision=0
        )
        == 1
    )
    assert (await store.equipment_docs(scope))[BOX_SLUG]["mass"] == 5.0
    edited = await store.fingerprint(scope)
    assert edited != seeded
    assert await store.set_equipment_cad(scope, box["id"], "_equipment/x/source.stl")
    assert await store.fingerprint(scope) != edited
    assert await store.equipment_cad_keys(scope) == {BOX_SLUG: "_equipment/x/source.stl"}
    # Another scope's catalog is its own.
    other = Scope.user("someone")
    assert BOX_SLUG in await store.equipment_docs(other)
    assert (await store.equipment_docs(other))[BOX_SLUG]["mass"] != 5.0


def test_a_plain_box_compiles_locally_with_the_catalog_mass(client: TestClient, tmp_path: pathlib.Path):
    """Add equipment needs no type of its own: the seeded plain box resolves from the file
    catalog in the in-process (default engine) compile."""
    import time

    client.get(EQ)  # seed
    model_id = client.post(PM, json={"name": "box-equipment"}).json()["id"]
    doc = {
        "spaces": [{"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}],
        "equipments": [
            {
                "INCLUDE": True,
                "NAME": "EQ1",
                "SPACE_NAME": "Cell1",
                "SPACE_LOC": "FLOOR",
                "GLOBAL_COORDS": True,
                "DESCRIPTION": BOX_SLUG,
                "X": 1,
                "Y": 1,
                "Z": 0,
                "LX": 1,
                "LY": 1,
                "LZ": 1,
                "COGx": 0,
                "COGy": 0,
                "COGz": 0.5,
                "massDry": 0,
                "massCont": 0,
            }
        ],
    }
    r = client.put(f"{PM}/{model_id}", json={"doc": doc, "base_revision": 0})
    assert r.status_code == 200, r.text
    started = client.post(f"{PM}/{model_id}/compile").json()
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{started['job_id']}").json()
        if status["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.25)
    assert status["status"] == "done", status
    # Built from the catalog: the artifact carries the catalog fingerprint sidecar.
    from ada.comms.rest.procedural import procedural_catalog_fp_key

    sidecar = tmp_path / "shared" / pathlib.PurePosixPath(procedural_catalog_fp_key(started["derived_key"]))
    assert sidecar.is_file()
