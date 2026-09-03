"""``GET /admin/audit/{id}/source`` falls back to the preserved copy.

Capture exists so a failed row stays reproducible after its source is gone;
that is only true if the download route actually reaches for the preserved
copy. Pinning it here keeps every consumer — the CLI's ``audit fetch`` /
``audit repro`` and the admin panel alike — on one code path, so none of them
needs to know the failure corpus exists.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import db as dbm  # noqa: E402
from ada.comms.rest import failure_capture  # noqa: E402
from ada.comms.rest import storage as storage_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)

PRESERVED = b"the exact bytes that broke the converter"
ORIGINAL_KEY = "decks/gone.xml"
FAILURE_KEY = "0123456789abcdef0123456789abcdef.xml"


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


def _row(**over):
    row = {
        "id": 1,
        "scope_kind": "user",
        "scope_id": "sub-abc",
        "key": ORIGINAL_KEY,
        "failure_key": None,
        "status": "error",
    }
    row.update(over)
    return row


@pytest.fixture
def client_with_row(monkeypatch, tmp_path):
    """App whose audit row is synthetic and whose original source is gone."""

    def _make(row):
        async def fake_get_audit_by_id(pool, audit_id):
            return row

        monkeypatch.setattr(dbm, "get_audit_by_id", fake_get_audit_by_id)

        async def fake_open_stream(self, scope, key):
            # The original is gone; only the preserved copy resolves.
            if scope.kind == "corpus" and key == FAILURE_KEY:

                async def _stream():
                    yield PRESERVED

                return storage_module.StreamResult(stream=_stream(), content_encoding=None)
            raise FileNotFoundError(f"Object at location {scope.prefix()}/{key} not found")

        monkeypatch.setattr(storage_module.Storage, "open_stream", fake_open_stream)

        app = create_app(_settings(tmp_path))
        client = TestClient(app)
        client.__enter__()
        app.state.db_pool = object()
        return client

    return _make


def test_a_row_with_a_preserved_copy_still_downloads(client_with_row):
    client = client_with_row(_row(failure_key=FAILURE_KEY))
    r = client.get("/api/admin/audit/1/source")
    assert r.status_code == 200, r.text
    assert r.content == PRESERVED
    # Named for the key the failure happened on, not the content-addressed one —
    # the operator downloading it is investigating `gone.xml`.
    assert "gone.xml" in r.headers.get("content-disposition", "")


def test_a_row_without_a_preserved_copy_still_404s(client_with_row):
    """No capture (disabled, ineligible, or source already gone) → unchanged."""
    client = client_with_row(_row(failure_key=None))
    assert client.get("/api/admin/audit/1/source").status_code == 404


def test_a_preserved_copy_that_is_itself_missing_404s(client_with_row):
    """A pointer to a blob someone pruned must not 500."""
    client = client_with_row(_row(failure_key="deadbeef.xml"))
    assert client.get("/api/admin/audit/1/source").status_code == 404


def test_the_fallback_reads_from_the_admin_only_corpus(client_with_row):
    """The preserved copy lives in a scope only admins can address."""
    assert failure_capture.failure_scope().kind == "corpus"
    client = client_with_row(_row(failure_key=FAILURE_KEY))
    assert client.get("/api/admin/audit/1/source").status_code == 200
