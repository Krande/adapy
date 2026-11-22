"""Robot-asset bundles (``.adabots``): the export of the provider store's robot assets, the validation
of an uploaded bundle, the preview and the import (``ada/comms/rest/robot_bundles.py``,
``routes/admin_robot_assets.py``; ``docs/architecture/robot_asset_bundles.md``).

The robots here are synthetic: a robot.json and two small "meshes" each. Local storage, no database,
auth off (every request is the local admin).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import tempfile
import zipfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi.testclient import TestClient

import ada.comms.rest.auth as _auth
from ada.comms.rest import robot_bundles as rb
from ada.comms.rest.app import create_app
from ada.comms.rest.config import AuthConfig, LocalConfig, QueueConfig, Settings
from ada.comms.rest.storage import Storage

BASE = "/api/admin/robot-assets"


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


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def robot_files(robot_id: str, version: str, *, salt: str = "") -> dict[str, bytes]:
    """A synthetic robot asset: two meshes and the robot.json that pins them."""
    meshes = {
        "meshes/coarse/base_link.stl": f"solid base {robot_id} {salt}\nendsolid\n".encode(),
        "meshes/coarse/link_1.stl": f"solid link1 {robot_id} {salt}\nendsolid\n".encode() * 50,
    }
    doc = {
        "schema": "example/robot-asset/1",
        "id": robot_id,
        "version": version,
        "label": f"Test arm {robot_id} with a rather long descriptive name",
        "roles": ["picker"],
        "payload_kg": 5.0,
        "reach_m": 0.9,
        "meshes": {
            "levels": {
                "coarse": {
                    "links": {
                        p.rsplit("/", 1)[-1][:-4]: {"path": p, "sha256": _sha(b), "bytes": len(b)}
                        for p, b in meshes.items()
                    }
                }
            }
        },
    }
    return {**meshes, "robot.json": json.dumps(doc, indent=1).encode()}


async def _put(storage: Storage, robot_id: str, version: str, files: dict[str, bytes], *, gz: bool = False) -> None:
    prefix = rb.asset_prefix(robot_id, version)
    for p, b in files.items():
        if p == "robot.json":
            continue
        await storage.put_bytes(rb.STORE, prefix + p, b, content_encoding="gzip" if gz else None)
    if "robot.json" in files:
        await storage.put_bytes(rb.STORE, prefix + "robot.json", files["robot.json"])


def store_robot(tmp_path, robot_id, version, *, salt="", gz=False, files=None):
    storage = Storage.from_settings(_settings(tmp_path))
    asyncio.run(_put(storage, robot_id, version, files or robot_files(robot_id, version, salt=salt), gz=gz))


def stored_tree(tmp_path) -> dict[str, str]:
    """``{key: sha256 of the logical bytes}`` of every robot asset file in the store."""
    storage = Storage.from_settings(_settings(tmp_path))

    async def go():
        out = {}
        for e in await storage.list_prefix(rb.STORE, rb.STORE_PREFIX + "/"):
            if e.key == rb.INDEX_KEY:
                continue
            out[e.key] = _sha(await storage.get_bytes(rb.STORE, e.key))
        return out

    return asyncio.run(go())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")  # 2026-01-01
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as c:
        yield c


def export(client, **params) -> bytes:
    q = "&".join(f"ref={r}" for r in params.get("refs", [])) or "all=1"
    r = client.get(f"{BASE}/export?{q}")
    assert r.status_code == 200, r.text
    return r.content


def preview(client, data: bytes) -> dict:
    r = client.post(f"{BASE}/import/preview", content=data, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    return r.json()


def by_ref(rows):
    return {r["ref"]: r for r in rows}


# --- versions --------------------------------------------------------------------------------------


def test_versions_order_as_numbers_and_semver():
    order = ["1", "2", "10"]
    assert sorted(["10", "2", "1"], key=rb.version_key) == order
    assert sorted(["1.10.0", "1.9.0", "1.0.0-rc1", "1.0.0"], key=rb.version_key) == [
        "1.0.0-rc1",
        "1.0.0",
        "1.9.0",
        "1.10.0",
    ]
    assert rb.version_key("1") < rb.version_key("1.1")
    assert rb.latest_version(["3", "12", "9"]) == "12"
    assert rb.version_scheme("4") == "revision"
    assert rb.version_scheme("1.2.3") == "semver"
    assert rb.version_scheme("2024a") == "natural"


# --- listing and export ----------------------------------------------------------------------------


def test_the_list_has_each_robot_with_its_versions_newest_first(tmp_path, client):
    store_robot(tmp_path, "arm-a", "1")
    store_robot(tmp_path, "arm-a", "2", salt="v2")
    store_robot(tmp_path, "arm-b", "1")
    storage = Storage.from_settings(_settings(tmp_path))
    # An incomplete version (no robot.json) is not listed.
    asyncio.run(storage.put_bytes(rb.STORE, rb.asset_prefix("arm-b", "2") + "meshes/x.stl", b"x"))
    body = client.get(BASE).json()
    robots = {r["id"]: r for r in body["robots"]}
    assert set(robots) == {"arm-a", "arm-b"}
    assert robots["arm-a"]["latest"] == "2"
    assert [v["version"] for v in robots["arm-a"]["versions"]] == ["2", "1"]
    assert robots["arm-a"]["versions"][0]["metadata"]["roles"] == ["picker"]
    assert [v["version"] for v in robots["arm-b"]["versions"]] == ["1"]
    assert body["bundle"]["suffix"] == ".adabots"
    assert body["bundle"]["schema"] == "ada.robots/bundle@1"


def test_an_export_is_a_zip_with_a_manifest_and_one_folder_per_robot(tmp_path, client):
    store_robot(tmp_path, "arm-a", "1", gz=True)  # meshes gzipped at rest: exported as their own bytes
    store_robot(tmp_path, "arm-b", "3")
    r = client.get(f"{BASE}/export?ref=arm-a@1")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="arm-a@1.adabots"'
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert names[-1] == "manifest.json"
    assert all(n.startswith("robots/arm-a/1/") for n in names[:-1])
    m = json.loads(zf.read("manifest.json"))
    assert m["schema"] == "ada.robots/bundle@1"
    assert m["exported_at"] == "2026-01-01T00:00:00Z"
    assert m["adapy_version"]
    (robot,) = m["robots"]
    assert robot["ref"] == "arm-a@1" and robot["version_scheme"] == "revision"
    assert robot["latest_in_source"] is True
    assert robot["name"].startswith("Test arm arm-a")
    src = robot_files("arm-a", "1")
    assert {f["path"]: f["sha256"] for f in robot["files"]} == {p: _sha(b) for p, b in src.items()}
    for p, b in src.items():
        assert zf.read(f"robots/arm-a/1/{p}") == b
    assert robot["content_sha256"] == rb.content_sha256((p, _sha(b)) for p, b in src.items())


def test_the_same_assets_export_to_the_same_bytes(tmp_path, client):
    store_robot(tmp_path, "arm-a", "1")
    store_robot(tmp_path, "arm-b", "1")
    first, second = export(client), export(client)
    assert first == second
    # The order of the refs asked for does not matter.
    assert export(client, refs=["arm-b@1", "arm-a@1"]) == export(client, refs=["arm-a@1", "arm-b@1"])


def test_export_selection_single_many_all_and_by_id(tmp_path, client):
    store_robot(tmp_path, "arm-a", "1")
    store_robot(tmp_path, "arm-a", "2", salt="2")
    store_robot(tmp_path, "arm-b", "1")

    def refs(data):
        return [r["ref"] for r in json.loads(zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))["robots"]]

    assert refs(export(client, refs=["arm-b@1"])) == ["arm-b@1"]
    assert refs(export(client, refs=["arm-a"])) == ["arm-a@1", "arm-a@2"]
    assert refs(export(client)) == ["arm-a@1", "arm-a@2", "arm-b@1"]
    r = client.post(f"{BASE}/export", json={"refs": ["arm-a@2", "arm-b@1"]})
    assert r.status_code == 200 and refs(r.content) == ["arm-a@2", "arm-b@1"]
    assert r.headers["content-disposition"].endswith('-2.adabots"')
    assert client.post(f"{BASE}/export", json={"all": True}).status_code == 200
    assert client.get(f"{BASE}/export?ref=arm-z@1").status_code == 404
    assert client.get(f"{BASE}/export?ref=Not%20a%20ref").status_code == 400
    assert client.get(f"{BASE}/export").status_code == 400
    assert client.post(f"{BASE}/export", json={"refs": []}).status_code == 400


def test_export_of_an_empty_store_is_404(client):
    assert client.get(f"{BASE}/export?all=1").status_code == 404


# --- round trip --------------------------------------------------------------------------------------


def test_round_trip_into_an_empty_store_gives_identical_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1767225600")
    src_dir, dst_dir = tmp_path / "src", tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    store_robot(src_dir, "arm-a", "1", gz=True)
    store_robot(src_dir, "arm-a", "2", salt="2")
    store_robot(src_dir, "arm-b", "1.0.0")
    with TestClient(create_app(_settings(src_dir))) as src:
        bundle = export(src)
    with TestClient(create_app(_settings(dst_dir))) as dst:
        p = preview(dst, bundle)
        assert {r["status"] for r in p["robots"]} == {"new"}
        r = dst.post(f"{BASE}/import/{p['upload_id']}", json={})
        assert r.status_code == 200, r.text
        assert {x["result"] for x in r.json()["results"]} == {"added"}
        assert r.json()["index_written"] is True
        again = export(dst)
    assert stored_tree(dst_dir) == stored_tree(src_dir)
    assert again == bundle, "export -> import -> export gives the same bundle"
    index = json.loads(asyncio.run(Storage.from_settings(_settings(dst_dir)).get_bytes(rb.STORE, rb.INDEX_KEY)))
    assert [a["ref"] for a in index["assets"]] == ["arm-a@1", "arm-a@2", "arm-b@1.0.0"]
    assert index["assets"][0]["roles"] == ["picker"] and index["assets"][0]["levels"] == ["coarse"]


def test_reimporting_an_export_into_the_same_store_is_all_unchanged(tmp_path, client):
    store_robot(tmp_path, "arm-a", "1", gz=True)
    store_robot(tmp_path, "arm-b", "1")
    before = stored_tree(tmp_path)
    p = preview(client, export(client))
    assert {r["ref"]: r["status"] for r in p["robots"]} == {"arm-a@1": "unchanged", "arm-b@1": "unchanged"}
    assert p["counts"]["unchanged"] == 2
    r = client.post(f"{BASE}/import/{p['upload_id']}", json={}).json()
    assert {x["result"] for x in r["results"]} == {"skipped"}
    assert r["index_written"] is False
    assert stored_tree(tmp_path) == before


# --- the cases of an import ---------------------------------------------------------------------------


def _bundle_of(tmp_path, robots: list[tuple[str, str, str]]) -> bytes:
    """Export ``robots`` ((id, version, salt)) from a store of their own."""
    d = tmp_path / f"b{len(list(tmp_path.iterdir()))}"
    d.mkdir()
    for rid, v, salt in robots:
        store_robot(d, rid, v, salt=salt)
    with TestClient(create_app(_settings(d))) as c:
        return export(c)


def test_preview_says_new_unchanged_newer_older_and_conflict(tmp_path, client):
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    # The store: arm-u@1 (same as the bundle's), arm-n@1 (the bundle has 2), arm-o@3 (the bundle has 2),
    # arm-c@1 (the bundle's arm-c@1 has other content).
    for rid, v in (("arm-u", "1"), ("arm-n", "1"), ("arm-o", "3"), ("arm-c", "1")):
        store_robot(tmp_path, rid, v)
    data = _bundle_of(
        store_dir,
        [
            ("arm-new", "1", ""),
            ("arm-u", "1", ""),
            ("arm-n", "2", "two"),
            ("arm-o", "2", "two"),
            ("arm-c", "1", "changed"),
        ],
    )
    before = stored_tree(tmp_path)
    p = preview(client, data)
    rows = by_ref(p["robots"])
    assert rows["arm-new@1"]["status"] == "new"
    assert rows["arm-u@1"]["status"] == "unchanged"
    assert rows["arm-n@2"]["status"] == "newer" and "adds version 2" in rows["arm-n@2"]["detail"]
    assert rows["arm-n@2"]["latest_before"] == "1"
    assert rows["arm-o@2"]["status"] == "older" and rows["arm-o@2"]["latest_before"] == "3"
    assert rows["arm-c@1"]["status"] == "conflict"
    assert p["counts"] == {"new": 1, "newer": 1, "unchanged": 1, "older": 1, "conflict": 1, "invalid": 0}
    assert stored_tree(tmp_path) == before, "a preview writes no asset"

    r = client.post(f"{BASE}/import/{p['upload_id']}", json={})
    results = by_ref(r.json()["results"])
    assert results["arm-new@1"]["result"] == "added"
    assert results["arm-n@2"]["result"] == "added"
    assert results["arm-u@1"]["result"] == "skipped"
    assert results["arm-o@2"]["result"] == "skipped"
    assert results["arm-c@1"]["result"] == "conflict"
    after = stored_tree(tmp_path)
    # The conflicting version is untouched; the old version of the newer robot is kept.
    for key in before:
        assert after[key] == before[key]
    assert any(k.startswith("assets/robots/arm-n/2/") for k in after)
    assert any(k.startswith("assets/robots/arm-n/1/") for k in after)
    assert not any(k.startswith("assets/robots/arm-o/2/") for k in after)
    listing = {x["id"]: x for x in client.get(BASE).json()["robots"]}
    assert listing["arm-n"]["latest"] == "2"
    assert [v["version"] for v in listing["arm-n"]["versions"]] == ["2", "1"]


def test_an_older_version_can_be_added_as_a_past_version_and_does_not_become_latest(tmp_path, client):
    store_robot(tmp_path, "arm-o", "3")
    src = tmp_path / "src"
    src.mkdir()
    data = _bundle_of(src, [("arm-o", "2", "two"), ("arm-o", "1", "one")])
    p = preview(client, data)
    assert {r["status"] for r in p["robots"]} == {"older"}
    r = client.post(
        f"{BASE}/import/{p['upload_id']}",
        json={"add_older": True, "robots": {"arm-o@1": {"add_older": False}}},
    ).json()
    results = by_ref(r["results"])
    assert results["arm-o@2"]["result"] == "added-older"
    assert "latest stays 3" in results["arm-o@2"]["detail"]
    assert results["arm-o@1"]["result"] == "skipped"
    listing = {x["id"]: x for x in client.get(BASE).json()["robots"]}
    assert listing["arm-o"]["latest"] == "3"
    assert [v["version"] for v in listing["arm-o"]["versions"]] == ["3", "2"]


def test_a_robot_left_out_in_the_preview_is_not_imported(tmp_path, client):
    src = tmp_path / "src"
    src.mkdir()
    p = preview(client, _bundle_of(src, [("arm-a", "1", ""), ("arm-b", "1", "")]))
    r = client.post(f"{BASE}/import/{p['upload_id']}", json={"robots": {"arm-b@1": {"include": False}}}).json()
    assert by_ref(r["results"])["arm-b@1"]["result"] == "skipped"
    assert [x["id"] for x in client.get(BASE).json()["robots"]] == ["arm-a"]


def test_an_import_compares_again_with_the_store_as_it_is_then(tmp_path, client):
    src = tmp_path / "src"
    src.mkdir()
    p = preview(client, _bundle_of(src, [("arm-a", "1", "")]))
    assert p["robots"][0]["status"] == "new"
    store_robot(tmp_path, "arm-a", "1", salt="meanwhile")
    r = client.post(f"{BASE}/import/{p['upload_id']}", json={}).json()
    assert r["results"][0]["result"] == "conflict"


def test_a_failed_robot_leaves_nothing_behind_and_the_others_are_imported(tmp_path, client, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    p = preview(client, _bundle_of(src, [("arm-a", "1", ""), ("arm-b", "1", "")]))
    real = Storage.put_path

    async def flaky(self, scope, key, path, **kw):
        if key.startswith("assets/robots/arm-a/") and key.endswith("robot.json"):
            raise RuntimeError("store went away")
        return await real(self, scope, key, path, **kw)

    monkeypatch.setattr(Storage, "put_path", flaky)
    r = client.post(f"{BASE}/import/{p['upload_id']}", json={}).json()
    results = by_ref(r["results"])
    assert results["arm-a@1"]["result"] == "failed" and "store went away" in results["arm-a@1"]["detail"]
    assert results["arm-b@1"]["result"] == "added"
    keys = stored_tree(tmp_path)
    assert not any(k.startswith("assets/robots/arm-a/") for k in keys), "nothing of the failed robot stays"
    assert any(k.startswith("assets/robots/arm-b/1/robot.json") for k in keys)


def test_an_incomplete_leftover_of_the_same_version_is_replaced(tmp_path, client):
    storage = Storage.from_settings(_settings(tmp_path))
    asyncio.run(storage.put_bytes(rb.STORE, rb.asset_prefix("arm-a", "1") + "meshes/stale.stl", b"old"))
    src = tmp_path / "src"
    src.mkdir()
    p = preview(client, _bundle_of(src, [("arm-a", "1", "")]))
    assert p["robots"][0]["status"] == "new"
    client.post(f"{BASE}/import/{p['upload_id']}", json={})
    keys = stored_tree(tmp_path)
    assert "assets/robots/arm-a/1/meshes/stale.stl" not in keys
    assert "assets/robots/arm-a/1/robot.json" in keys


def test_staged_uploads_are_found_by_id_and_can_be_dropped(tmp_path, client):
    src = tmp_path / "src"
    src.mkdir()
    p = preview(client, _bundle_of(src, [("arm-a", "1", "")]))
    assert client.delete(f"{BASE}/import/{p['upload_id']}").json() == {"removed": True}
    assert client.post(f"{BASE}/import/{p['upload_id']}", json={}).status_code == 404
    assert client.post(f"{BASE}/import/not-an-id", json={}).status_code == 400
    assert client.post(f"{BASE}/import/{'0' * 32}", json={"add_older": "yes"}).status_code == 400


def test_the_staging_sweep_removes_old_uploads(tmp_path):
    import datetime as dt

    storage = Storage.from_settings(_settings(tmp_path))
    asyncio.run(storage.put_bytes(rb.STORE, rb.staging_key("a" * 32), b"x"))
    later = dt.datetime.now(tz=dt.timezone.utc) + dt.timedelta(days=2)
    assert asyncio.run(rb.sweep_staging(storage, now=later)) == 1
    assert asyncio.run(storage.list_prefix(rb.STORE, rb.STAGING_PREFIX + "/")) == []


# --- validation ----------------------------------------------------------------------------------------


def _manifest_for(entries: dict[str, bytes], robot_id="arm-x", version="1") -> dict:
    base = f"robots/{robot_id}/{version}/"
    files = [
        {"path": n[len(base) :], "sha256": _sha(b), "bytes": len(b)} for n, b in entries.items() if n.startswith(base)
    ]
    return {"schema": rb.BUNDLE_SCHEMA, "robots": [{"id": robot_id, "version": version, "files": files}]}


def _zip(entries: dict[str, bytes], manifest: dict | None = None, *, extra: list[zipfile.ZipInfo] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n, b in entries.items():
            zf.writestr(n, b)
        for zi in extra:
            zf.writestr(zi, b"target")
        if manifest is not False:
            zf.writestr("manifest.json", json.dumps(manifest or _manifest_for(entries)))
    return buf.getvalue()


def _good_entries(robot_id="arm-x", version="1") -> dict[str, bytes]:
    return {f"robots/{robot_id}/{version}/{p}": b for p, b in robot_files(robot_id, version).items()}


def refused(client, data: bytes) -> list[str]:
    r = client.post(f"{BASE}/import/preview", content=data)
    assert r.status_code == 400, r.text
    return r.json()["detail"]["problems"]


def test_a_handmade_good_bundle_previews_as_new(client):
    p = preview(client, _zip(_good_entries()))
    assert p["robots"][0]["status"] == "new"


def test_not_a_zip_or_no_manifest_or_another_schema_is_refused(client):
    assert "not a zip" in refused(client, b"hello")[0]
    assert any("no manifest.json" in x for x in refused(client, _zip(_good_entries(), manifest=False)))
    m = _manifest_for(_good_entries())
    assert "is not a robot bundle" in refused(client, _zip(_good_entries(), {**m, "schema": "other/thing@1"}))[0]
    assert (
        "newer than this server reads"
        in refused(client, _zip(_good_entries(), {**m, "schema": "ada.robots/bundle@2"}))[0]
    )


def test_unsafe_paths_and_symlinks_are_refused(client):
    for name in ("../evil.json", "/abs/evil.json", "robots/a/../../evil.json", "C:/evil.json", "robots\\a\\x.json"):
        entries = {**_good_entries(), name: b"{}"}
        problems = refused(client, _zip(entries, _manifest_for(_good_entries())))
        # zipfile on Windows reads "\\" as "/": that name is then refused as one not in the manifest.
        shown = name.replace("\\", "/") if os.sep == "\\" else name
        assert any(shown in p for p in problems), (name, problems)
    link = zipfile.ZipInfo("robots/arm-x/1/meshes/link.stl")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    problems = refused(client, _zip(_good_entries(), _manifest_for(_good_entries()), extra=[link]))
    assert any("symbolic links" in p for p in problems)


def test_files_not_in_the_manifest_or_of_a_type_not_allowed_are_refused(client):
    entries = {**_good_entries(), "robots/arm-x/1/notes.exe": b"MZ"}
    assert any("not in its manifest" in p for p in refused(client, _zip(entries, _manifest_for(_good_entries()))))
    assert any("not an allowed file type" in p for p in refused(client, _zip(entries)))


def test_a_file_whose_bytes_are_not_the_manifests_makes_its_robot_invalid(client):
    entries = _good_entries()
    m = _manifest_for(entries)
    entries["robots/arm-x/1/meshes/coarse/base_link.stl"] = b"solid tampered\nendsolid\n"
    p = preview(client, _zip(entries, m))
    (row,) = p["robots"]
    assert row["status"] == "invalid"
    assert any("base_link.stl" in x for x in row["problems"])
    r = client.post(f"{BASE}/import/{p['upload_id']}", json={}).json()
    assert r["results"][0]["result"] == "skipped"
    assert client.get(BASE).json()["robots"] == []


def test_a_robot_json_that_is_not_where_it_sits_or_pins_other_bytes_is_invalid(client):
    entries = _good_entries()
    doc = json.loads(entries["robots/arm-x/1/robot.json"])
    doc["id"] = "someone-else"
    entries["robots/arm-x/1/robot.json"] = json.dumps(doc).encode()
    (row,) = preview(client, _zip(entries))["robots"]
    assert row["status"] == "invalid" and any("not arm-x@1" in x for x in row["problems"])

    entries = _good_entries()
    doc = json.loads(entries["robots/arm-x/1/robot.json"])
    doc["meshes"]["levels"]["coarse"]["links"]["base_link"]["sha256"] = "0" * 64
    entries["robots/arm-x/1/robot.json"] = json.dumps(doc).encode()
    (row,) = preview(client, _zip(entries))["robots"]
    assert row["status"] == "invalid" and any("another sha256" in x for x in row["problems"])


def test_limits_on_size_and_file_count(client, monkeypatch):
    monkeypatch.setenv("ADA_ROBOT_BUNDLE_MAX_FILES", "2")
    assert "entries" in refused(client, _zip(_good_entries()))[0]
    monkeypatch.delenv("ADA_ROBOT_BUNDLE_MAX_FILES")
    monkeypatch.setenv("ADA_ROBOT_BUNDLE_MAX_FILE_BYTES", "100")
    assert any("the limit is 100" in p for p in refused(client, _zip(_good_entries())))
    monkeypatch.delenv("ADA_ROBOT_BUNDLE_MAX_FILE_BYTES")
    monkeypatch.setenv("ADA_ROBOT_BUNDLE_MAX_BYTES", "100")
    r = client.post(f"{BASE}/import/preview", content=_zip(_good_entries()))
    assert r.status_code == 413
    assert client.post(f"{BASE}/import/preview", content=b"").status_code in (400, 413)


def test_a_zip_bomb_is_refused_by_its_ratio(client):
    entries = _good_entries()
    entries["robots/arm-x/1/meshes/coarse/huge.stl"] = b"\0" * (8 << 20)
    problems = refused(client, _deflated(entries))
    assert any("unpacks to" in p for p in problems)


def _deflated(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for n, b in entries.items():
            zf.writestr(n, b)
        zf.writestr("manifest.json", json.dumps(_manifest_for(entries)))
    return buf.getvalue()


def test_manifest_shape_errors_are_refused(client):
    entries = _good_entries()
    for robots in ("nope", [{"id": "BAD ID", "version": "1", "files": []}], [{"id": "a", "version": "1"}]):
        problems = refused(client, _zip(entries, {"schema": rb.BUNDLE_SCHEMA, "robots": robots}))
        assert problems


def test_a_member_may_not_use_the_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _auth.User,
        "local_dev",
        classmethod(lambda cls: cls(sub="m", email="m@x", display_name="M", groups=frozenset(), is_admin=False)),
    )
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as c:
        assert c.get(BASE).status_code == 403
        assert c.get(f"{BASE}/export?all=1").status_code == 403
        assert c.post(f"{BASE}/import/preview", content=b"x").status_code == 403


def test_gzip_at_rest_does_not_change_the_content_hash(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    store_robot(a, "arm-a", "1", gz=True)
    store_robot(b, "arm-a", "1", gz=False)

    def content(d):
        storage = Storage.from_settings(_settings(d))
        store = asyncio.run(rb.list_store(storage))
        return asyncio.run(rb.stored_content(storage, store["arm-a"]["1"]))[0]

    assert content(a) == content(b)
