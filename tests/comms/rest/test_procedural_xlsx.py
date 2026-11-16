"""Procedural model Excel export / import: key conventions, the dependency-free
``_ADA_META`` reader, and the endpoint shapes: without a database (a local viewer)
export and import run in-process on file-stored models; upload + engine
auto-detection needs neither DB nor ada."""

from __future__ import annotations

import io
import os
import pathlib
import tempfile

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.converter import is_hidden_key  # noqa: E402
from ada.comms.rest.procedural import (  # noqa: E402
    ADA_META_SHEET,
    procedural_import_result_key,
    procedural_import_source_key,
    procedural_xlsx_export_key,
    read_ada_meta_from_xlsx_bytes,
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
        auth=AuthConfig(enabled=False, issuer="", client_id="", audience="", admin_group="", cli_token_secret=""),
        database_url=database_url,
    )


def _xlsx_with_meta(rows: list[tuple[str, str]]) -> bytes:
    """A minimal workbook carrying an ``_ADA_META`` key/value sheet — built with
    openpyxl directly (no ada), so the reader is exercised standalone."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Spaces"
    ws = wb.create_sheet(ADA_META_SHEET)
    for r, (k, v) in enumerate(rows, start=1):
        ws.cell(row=r, column=1, value=k)
        ws.cell(row=r, column=2, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── standalone helpers ───────────────────────────────────────────────


def test_export_key_shape():
    key = procedural_xlsx_export_key("abc-123", 4)
    assert key == "_procedural/abc-123/r4.xlsx"
    assert is_hidden_key(key)
    # non-default engine gets its own suffixed key
    assert procedural_xlsx_export_key("abc-123", 4, "ext-engine") == "_procedural/abc-123/r4.ext-engine.xlsx"
    assert procedural_xlsx_export_key("abc-123", 4, "adapy-default") == key  # collapses to bare


def test_import_key_shapes():
    src = procedural_import_source_key("tok123")
    assert src == "_procedural/_import/tok123/source.xlsx"
    assert is_hidden_key(src)
    assert procedural_import_result_key(src) == "_procedural/_import/tok123/result.json"


def test_ada_meta_reader_roundtrips():
    data = _xlsx_with_meta([("ada_meta_version", "1"), ("engine", "ext-engine"), ("package_version", "0.10.2")])
    meta = read_ada_meta_from_xlsx_bytes(data)
    assert meta is not None
    assert meta["engine"] == "ext-engine"
    assert meta["package_version"] == "0.10.2"


def test_ada_meta_reader_absent_and_malformed():
    # A workbook with no _ADA_META sheet -> None (the "prompt for engine" case).
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Spaces"
    buf = io.BytesIO()
    wb.save(buf)
    assert read_ada_meta_from_xlsx_bytes(buf.getvalue()) is None
    # Junk / not-a-zip -> None, never raises.
    assert read_ada_meta_from_xlsx_bytes(b"not a workbook") is None
    assert read_ada_meta_from_xlsx_bytes(b"") is None


# ── no-DB API path ───────────────────────────────────────────────────


@pytest.fixture
def app_client(tmp_path: pathlib.Path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as client:
        yield client


BASE = "/api/scopes/shared/procedural-models"
DOC = {"spaces": [{"NAME": "Cell1", "X": 0, "Y": 0, "Z": 0, "DX": 5, "DY": 5, "DZ": 3}]}


def _wait_job(client: TestClient, job_id: str, timeout: float = 120.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    status: dict = {}
    while time.monotonic() < deadline:
        status = client.get(f"/api/convert/{job_id}").json()
        if status["status"] in ("done", "error", "cancelled"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish: {status}")


def _model(client: TestClient, name: str) -> str:
    model_id = client.post(BASE, json={"name": name}).json()["id"]
    assert client.put(f"{BASE}/{model_id}", json={"doc": DOC, "base_revision": 0}).status_code == 200
    return model_id


def test_export_xlsx_runs_in_process_without_a_db(app_client: TestClient, tmp_path: pathlib.Path):
    """A local viewer (no database, no queue) exports a file-stored model to its
    engine's workbook in this process (local_jobs.start_procedural_model_job)."""
    from openpyxl import load_workbook

    model_id = _model(app_client, "local-export")
    r = app_client.post(f"{BASE}/{model_id}/export-xlsx")
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["job_id"].startswith("local-") and started["cached"] is False
    assert started["derived_key"] == procedural_xlsx_export_key(model_id, 1)
    status = _wait_job(app_client, started["job_id"])
    assert status["status"] == "done", status

    data = (tmp_path / "shared" / pathlib.PurePosixPath(started["derived_key"])).read_bytes()
    wb = load_workbook(io.BytesIO(data))
    assert ADA_META_SHEET in wb.sheetnames
    assert read_ada_meta_from_xlsx_bytes(data) is not None
    # Exported once: the same revision is the cached workbook.
    again = app_client.post(f"{BASE}/{model_id}/export-xlsx").json()
    assert again == {"job_id": None, "derived_key": started["derived_key"], "cached": True}
    assert app_client.post(f"{BASE}/{model_id}/export-xlsx", params={"engine": "echo"}).status_code == 400
    assert app_client.post(f"{BASE}/not-a-model/export-xlsx").status_code == 404


def test_export_model_runs_in_process_without_a_db(app_client: TestClient, tmp_path: pathlib.Path):
    """IFC / Genie export of a file-stored model, in this process, equipment from the file catalog."""
    model_id = _model(app_client, "local-model-export")
    r = app_client.post(f"{BASE}/{model_id}/export-model", params={"format": "gxml"})
    assert r.status_code == 200, r.text
    started = r.json()
    assert started["job_id"].startswith("local-")
    status = _wait_job(app_client, started["job_id"])
    assert status["status"] == "done", status
    assert (tmp_path / "shared" / pathlib.PurePosixPath(started["derived_key"])).stat().st_size > 0


def test_import_xlsx_round_trips_without_a_db(app_client: TestClient, tmp_path: pathlib.Path):
    """The exported workbook imports back into a NEW file-stored model, in this process."""
    import json

    model_id = _model(app_client, "round-trip-src")
    started = app_client.post(f"{BASE}/{model_id}/export-xlsx").json()
    assert _wait_job(app_client, started["job_id"])["status"] == "done"
    data = (tmp_path / "shared" / pathlib.PurePosixPath(started["derived_key"])).read_bytes()

    up = app_client.post(f"{BASE}/import-xlsx/upload", content=data).json()
    body = {"source_key": up["source_key"], "engine": up["engine"], "name": "round-trip"}
    r = app_client.post(f"{BASE}/import-xlsx", json=body)
    assert r.status_code == 201, r.text
    job = r.json()
    status = _wait_job(app_client, job["job_id"])
    assert status["status"] == "done", status
    result = json.loads(app_client.get(f"/api/scopes/shared/blobs/{job['derived_key']}").content)
    assert result["name"] == "round-trip" and result["revision"] == 1

    got = app_client.get(f"{BASE}/{result['model_id']}").json()
    assert got["name"] == "round-trip" and got["revision"] == 1
    assert [s["NAME"] for s in got["doc"]["spaces"]] == ["Cell1"]
    assert got["doc"]["source_xlsx_key"]
    assert sorted(m["name"] for m in app_client.get(BASE).json()["models"]) == ["round-trip", "round-trip-src"]

    # The name is scope-unique, as in postgres: a second import of it fails, and says why.
    r = app_client.post(f"{BASE}/import-xlsx", json=body)
    status = _wait_job(app_client, r.json()["job_id"])
    assert status["status"] == "error" and "already exists" in (status["error"] or "")


def test_stats_export_reads_the_sidecar_without_a_db(app_client: TestClient, tmp_path: pathlib.Path):
    import json

    from ada.comms.rest.procedural import procedural_glb_key, procedural_stats_key

    model_id = _model(app_client, "stats-export")
    glb_key = procedural_glb_key(model_id, 1)
    url = f"{BASE}/{model_id}/stats/export"
    assert app_client.get(url, params={"key": glb_key}).status_code == 404
    stats = {"disciplines": [{"name": "structural", "mass": 1.0, "count": 2}], "total_mass": 1.0}
    sidecar = tmp_path / "shared" / pathlib.PurePosixPath(procedural_stats_key(glb_key))
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(stats), encoding="utf-8")

    r = app_client.get(url, params={"key": glb_key})
    assert r.status_code == 200, r.text
    assert r.content[:2] == b"PK"
    assert "stats-export_stats.xlsx" in r.headers["content-disposition"]
    r = app_client.get(url, params={"key": glb_key, "fmt": "csv"})
    assert r.status_code == 200 and "Mass by discipline" in r.text


def test_propose_relocations_runs_in_process_without_a_db(app_client: TestClient):
    import json

    model_id = _model(app_client, "relocations")
    r = app_client.post(f"{BASE}/{model_id}/propose-relocations")
    assert r.status_code == 200, r.text
    status = _wait_job(app_client, r.json()["job_id"])
    assert status["status"] == "done", status
    result = json.loads(app_client.get(f"/api/scopes/shared/blobs/{r.json()['derived_key']}").content)
    assert "proposals" in result


_TOOL_ENGINE = """
import io


def compile(doc, **kwargs):
    raise NotImplementedError


def export_xlsx(doc, name=None):
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "ToolSheet"
    wb.active["A1"] = name
    wb.active["A2"] = len(doc.get("spaces") or [])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
"""


def test_export_xlsx_uses_a_tool_engine_registered_in_this_process(
    app_client: TestClient, tmp_path: pathlib.Path, monkeypatch
):
    """A tool's engine registered at import (register_procedural_engine_capabilities, no
    database row) writes the workbook of a local export, as it compiles a local build."""
    from openpyxl import load_workbook

    from ada.topo_model import engine_catalog

    pkg = tmp_path / "pkgs"
    pkg.mkdir()
    (pkg / "local_tool_engine.py").write_text(_TOOL_ENGINE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(pkg))
    monkeypatch.setattr(engine_catalog, "_ENGINE_CAPABILITY_REGISTRY", dict(engine_catalog._ENGINE_CAPABILITY_REGISTRY))
    engine_catalog.register_procedural_engine_capabilities(
        "tool-engine", name="Tool engine", entrypoint="local_tool_engine:compile"
    )

    model_id = _model(app_client, "tool-export")
    started = app_client.post(f"{BASE}/{model_id}/export-xlsx", params={"engine": "tool-engine"}).json()
    assert started["derived_key"] == procedural_xlsx_export_key(model_id, 1, "tool-engine")
    status = _wait_job(app_client, started["job_id"])
    assert status["status"] == "done", status
    data = (tmp_path / "shared" / pathlib.PurePosixPath(started["derived_key"])).read_bytes()
    ws = load_workbook(io.BytesIO(data))["ToolSheet"]
    assert ws["A1"].value == "tool-export" and ws["A2"].value == 1


def test_a_local_export_resolves_an_engine_registered_in_this_process(monkeypatch):
    """No database: a plugin engine's manifest is the one this process advertises,
    as the in-process build resolves it."""
    import asyncio

    from ada.comms.rest.formats import procedural_support

    doc = {"kind": "server", "entrypoint": "some_tool.engine:compile"}
    monkeypatch.setattr(procedural_support, "_advertised_engine_doc", lambda slug: doc if slug == "tool" else None)
    row = {"scope_kind": "shared", "scope_id": None}
    resolve = procedural_support._resolve_engine_manifest
    assert asyncio.run(resolve(None, row, "tool")) is doc
    assert asyncio.run(resolve(None, row, "unknown")) is None
    assert asyncio.run(resolve(None, row, None)) is None


def test_a_queued_server_without_a_db_keeps_export_and_import_503(app_client: TestClient, monkeypatch):
    """With a queue the workers read models from postgres, so without one these stay 503."""
    from ada.comms.rest.queue import JobQueue

    monkeypatch.setattr(JobQueue, "enabled", property(lambda self: True))
    assert app_client.post(f"{BASE}/x/export-xlsx").status_code == 503
    r = app_client.post(
        f"{BASE}/import-xlsx",
        json={"source_key": "_procedural/_import/tok/source.xlsx", "engine": "adapy-default", "name": "m"},
    )
    assert r.status_code == 503


def test_import_upload_detects_engine_without_db(app_client: TestClient):
    # Upload + auto-detect needs neither a DB nor ada: the _ADA_META sheet is read
    # dependency-free and the workbook is staged under a hidden import token.
    data = _xlsx_with_meta([("engine", "ext-engine"), ("package_version", "0.10.2")])
    r = app_client.post("/api/scopes/shared/procedural-models/import-xlsx/upload", content=data)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == "ext-engine"
    assert body["source_key"].startswith("_procedural/_import/")

    # A legacy workbook (no _ADA_META) -> engine null (frontend then prompts).
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "Spaces"
    buf = io.BytesIO()
    wb.save(buf)
    r2 = app_client.post("/api/scopes/shared/procedural-models/import-xlsx/upload", content=buf.getvalue())
    assert r2.status_code == 200, r2.text
    assert r2.json()["engine"] is None


def test_import_upload_rejects_empty(app_client: TestClient):
    r = app_client.post("/api/scopes/shared/procedural-models/import-xlsx/upload", content=b"")
    assert r.status_code == 400


def test_import_enqueue_validates_source_key(app_client: TestClient):
    # A source_key not from the import-upload prefix, or no name, is rejected before any job runs.
    r = app_client.post(f"{BASE}/import-xlsx", json={"source_key": "bogus", "engine": "adapy-default", "name": "m"})
    assert r.status_code == 400
    r = app_client.post(f"{BASE}/import-xlsx", json={"source_key": "_procedural/_import/t/source.xlsx", "name": ""})
    assert r.status_code == 400


def test_a_local_model_export_is_bound_to_the_file_catalogs_fingerprint(app_client: TestClient, monkeypatch):
    """The in-process IFC / Genie export resolves equipment from the scope's catalog FILES, so, as
    the compile does, it binds its artifact to the fingerprint the route took of them: a catalog
    edit makes the cached export stale."""
    from ada.comms.rest.routes import procedural_models as routes

    async def _fp(pool, scope_obj, doc, store=None):
        assert store is not None  # the file catalogs, not only the (absent) pool
        return "catalog-fp-1"

    monkeypatch.setattr(routes, "catalog_fingerprint_for", _fp)
    model_id = _model(app_client, "local-export-catalog-fp")
    started = app_client.post(f"{BASE}/{model_id}/export-model", params={"format": "gxml"}).json()
    assert _wait_job(app_client, started["job_id"])["status"] == "done"
    assert app_client.post(f"{BASE}/{model_id}/export-model", params={"format": "gxml"}).json()["cached"] is True

    async def _fp2(pool, scope_obj, doc, store=None):
        return "catalog-fp-2"

    monkeypatch.setattr(routes, "catalog_fingerprint_for", _fp2)
    assert app_client.post(f"{BASE}/{model_id}/export-model", params={"format": "gxml"}).json()["cached"] is False
