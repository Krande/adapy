"""Presigned GETs a browser saves as a file: the name and the encoding ride in the URL.

A cross-origin anchor's ``download`` attribute is ignored, so ``POST /download-url`` with
``attachment`` signs S3's ``response-content-disposition`` (and a ``response-content-type``,
plus ``response-content-encoding: gzip`` for a gzip-at-rest object) into the URL. obstore's
``sign`` has no query parameters, so ``presign.resign_with_params`` re-signs obstore's URL:

* re-signing with nothing added reproduces obstore's own signature (so the algorithm is the
  one the store verifies), and re-signing with the overrides puts them under the signature;
* the route sanitises the name, decides the encoding by the same gzip-magic sniff as
  ``GET /blobs``, audits as before, and 503s (so the browser streams through the API) when
  the overrides cannot be signed;
* against a real S3 endpoint (``ADA_TEST_S3_*``, skipped otherwise) the store answers with
  the overridden headers and the bytes a browser would decode to the original.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import pathlib
import tempfile
import urllib.request
import uuid
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import obstore as obs
import pytest
from obstore.store import S3Store

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest import auth as auth_module  # noqa: E402
from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.config import (  # noqa: E402
    AuthConfig,
    LocalConfig,
    QueueConfig,
    Settings,
)
from ada.comms.rest.presign import (  # noqa: E402
    PresignOverridesUnavailable,
    content_disposition,
    resign_with_params,
    sanitize_download_filename,
)
from ada.comms.rest.routes.deps import RestContext  # noqa: E402
from ada.comms.rest.scope import Scope  # noqa: E402
from ada.comms.rest.storage import Storage  # noqa: E402

USER_SUB = "demo-user"
SECRET = "test-secret-key"


def _stores() -> list[S3Store]:
    """Path-style with a port, AWS virtual-hosted, AWS path-style, custom virtual-hosted, and a
    session token: each a different host / path / credential shape obstore signs."""
    common = {"access_key_id": "AKTEST", "secret_access_key": SECRET}
    return [
        S3Store("bkt", endpoint="http://localhost:3900", allow_http=True, region="garage", **common),
        S3Store("bkt", region="eu-west-1", virtual_hosted_style_request=True, **common),
        S3Store("bkt", region="eu-west-1", **common),
        S3Store("bkt", endpoint="https://s3.example.test", region="r", virtual_hosted_style_request=True, **common),
        S3Store("bkt", region="us-east-1", token="tok/en+with=specials", **common),
    ]


KEYS = ["plain/model.glb", "pre/users/a b/ø+x(1);=.glb", "deep/a%20b/~tilde/*star*.ifc"]


def _sig(url: str) -> str:
    return parse_qs(urlsplit(url).query)["X-Amz-Signature"][0]


# --- the signer --------------------------------------------------------------------------------


@pytest.mark.parametrize("store", _stores())
@pytest.mark.parametrize("key", KEYS)
def test_resign_without_params_reproduces_obstore_signature(store, key):
    url = obs.sign(store, "GET", key, timedelta(seconds=900))
    assert _sig(resign_with_params(url, SECRET, {})) == _sig(url)


@pytest.mark.parametrize("store", _stores())
def test_resign_signs_the_overrides_into_the_url(store):
    url = obs.sign(store, "GET", KEYS[1], timedelta(seconds=900))
    disposition = content_disposition("ø model.glb")
    signed = resign_with_params(url, SECRET, {"response-content-disposition": disposition})
    query = parse_qs(urlsplit(signed).query)
    assert query["response-content-disposition"] == [disposition]
    # Same scope, date, expiry and path as obstore's; a different signature (it covers the param).
    for k in ("X-Amz-Credential", "X-Amz-Date", "X-Amz-Expires"):
        assert query[k] == parse_qs(urlsplit(url).query)[k]
    assert urlsplit(signed).path == urlsplit(url).path
    assert _sig(signed) != _sig(url)
    # And the signature is a function of the param: a tampered value would not verify.
    other = resign_with_params(url, SECRET, {"response-content-disposition": content_disposition("evil.exe")})
    assert _sig(other) != _sig(signed)


def test_resign_refuses_a_url_it_did_not_come_from():
    with pytest.raises(ValueError):
        resign_with_params("https://example.test/k?X-Amz-Algorithm=AWS4-HMAC-SHA1", SECRET, {})


# --- the name ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("model.glb", "model.glb"),
        ("folder/sub/model.glb", "model.glb"),
        ("..\\..\\windows\\evil.bat", "evil.bat"),
        ('a"b\r\nc.ifc', "a_b__c.ifc"),
        ("  ..hidden.  ", "hidden"),
        ("nul\x00byte.txt", "nul_byte.txt"),
        ("ø æ 模型.step", "ø æ 模型.step"),
        ("", "fallback.glb"),
        (None, "fallback.glb"),
        ("../..", "fallback.glb"),
        ('"""', "fallback.glb"),
    ],
)
def test_sanitize_download_filename(raw, expected):
    assert sanitize_download_filename(raw, "keys/fallback.glb") == expected


def test_sanitize_caps_length_and_keeps_the_extension():
    name = sanitize_download_filename("x" * 500 + ".glb")
    assert len(name) <= 180 and name.endswith(".glb")
    assert sanitize_download_filename("", "") == "download"


def test_content_disposition_has_an_ascii_fallback_and_the_exact_utf8_name():
    assert content_disposition("model.glb") == "attachment; filename=\"model.glb\"; filename*=UTF-8''model.glb"
    assert content_disposition("ø x;1.glb") == (
        "attachment; filename=\"_ x;1.glb\"; filename*=UTF-8''%C3%B8%20x%3B1.glb"
    )
    assert content_disposition("a.glb", attachment=False).startswith("inline; ")


# --- Storage.presigned_get_url -----------------------------------------------------------------


def _s3_storage(secret: str | None) -> Storage:
    store = S3Store(
        "bkt",
        endpoint="http://localhost:3900",
        allow_http=True,
        region="garage",
        access_key_id="AKTEST",
        secret_access_key=SECRET,
    )
    return Storage(store, prefix="base", presign_secret=secret)


def test_presigned_get_url_carries_the_overrides():
    storage = _s3_storage(SECRET)
    url = asyncio.run(
        storage.presigned_get_url(
            Scope.shared(),
            "a/model.glb",
            response_headers={"Content-Disposition": content_disposition("m.glb"), "Content-Encoding": "gzip"},
        )
    )
    query = parse_qs(urlsplit(url).query)
    assert query["response-content-disposition"] == [content_disposition("m.glb")]
    assert query["response-content-encoding"] == ["gzip"]
    assert urlsplit(url).path == "/bkt/base/shared/a/model.glb"
    # Without overrides the URL is obstore's, untouched.
    plain = asyncio.run(storage.presigned_get_url(Scope.shared(), "a/model.glb"))
    assert "response-" not in plain


def test_presigned_get_url_overrides_need_the_secret():
    storage = _s3_storage(None)
    with pytest.raises(PresignOverridesUnavailable):
        asyncio.run(
            storage.presigned_get_url(
                Scope.shared(), "a/model.glb", response_headers={"Content-Disposition": "attachment"}
            )
        )
    with pytest.raises(ValueError):
        asyncio.run(_s3_storage(SECRET).presigned_get_url(Scope.shared(), "k", response_headers={"X-Evil": "1"}))


# --- the route ---------------------------------------------------------------------------------


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
def client(tmp_path: pathlib.Path, monkeypatch):
    """A LocalStore app whose presign is stubbed to record what it was asked to sign."""
    monkeypatch.setattr(
        auth_module.User,
        "local_dev",
        classmethod(
            lambda cls: cls(
                sub=USER_SUB, email="demo@x.invalid", display_name="Demo", groups=frozenset(), is_admin=False
            )
        ),
    )
    monkeypatch.setattr(Storage, "supports_presigned_uploads", property(lambda self: True))
    signed: list[dict] = []

    async def _fake_presign(self, scope, key, expires_in_seconds=900, *, internal=False, response_headers=None):
        signed.append({"scope": scope, "key": key, "headers": response_headers})
        return f"https://object-store.invalid/{scope.prefix()}/{key}"

    monkeypatch.setattr(Storage, "presigned_get_url", _fake_presign)
    audits: list[dict] = []

    async def _audit(self, request, user, scope, action, **kw):
        audits.append({"action": action, **kw})

    monkeypatch.setattr(RestContext, "audit", _audit)
    app = create_app(_settings(tmp_path))
    with TestClient(app) as c:
        c.signed = signed
        c.audits = audits
        yield c


def _put(client: TestClient, key: str, data: bytes, **kw) -> None:
    storage: Storage = client.app.state.rest.storage
    asyncio.run(storage.put_bytes(Scope.user(USER_SUB), key, data, **kw))


def _download_url(client: TestClient, body: dict):
    return client.post("/api/scopes/user:me/download-url", json=body)


def test_attachment_on_a_plain_object(client):
    _put(client, "models/plain.glb", b"glTF" + b"\x00" * 64)
    r = _download_url(client, {"key": "models/plain.glb", "filename": "Project - plain.glb", "attachment": True})
    assert r.status_code == 200, r.text
    expected = content_disposition("Project - plain.glb")
    assert r.json()["content_disposition"] == expected
    assert client.signed[-1]["headers"] == {
        "Content-Disposition": expected,
        "Content-Type": "application/octet-stream",
    }
    assert client.audits == [{"action": "download", "key": "models/plain.glb", "status": "presigned"}]


def test_attachment_on_a_gzip_stored_object_forces_the_encoding(client):
    payload = b"ISO-10303-21;\n" * 200
    _put(client, "models/big.ifc", payload, content_encoding="gzip")
    r = _download_url(client, {"key": "models/big.ifc", "attachment": True})
    assert r.status_code == 200, r.text
    headers = client.signed[-1]["headers"]
    assert headers["Content-Encoding"] == "gzip"
    assert headers["Content-Type"] == "application/octet-stream"
    # No filename given: the key's basename.
    assert headers["Content-Disposition"] == content_disposition("big.ifc")


def test_the_filename_is_sanitised(client):
    _put(client, "models/a.glb", b"glTF....")
    r = _download_url(client, {"key": "models/a.glb", "filename": '../../etc/"pass"\r\n.glb', "attachment": True})
    assert r.status_code == 200, r.text
    assert r.json()["content_disposition"] == content_disposition("_pass___.glb")


def test_no_attachment_asked_means_no_overrides(client):
    """Programmatic readers (the streaming converters, the GLB loader) get the URL they always got."""
    _put(client, "models/a.glb", b"glTF....")
    r = _download_url(client, {"key": "models/a.glb"})
    assert r.status_code == 200, r.text
    assert r.json()["content_disposition"] is None
    assert client.signed[-1]["headers"] is None


def test_derived_keys_stay_unaudited(client):
    from ada.comms.rest.converters.keys import derived_key_for

    key = derived_key_for("models/a.ifc", "glb")
    _put(client, key, b"glTF....")
    r = _download_url(client, {"key": key, "filename": "a.glb", "attachment": True})
    assert r.status_code == 200, r.text
    assert client.audits == []


def test_overrides_that_cannot_be_signed_503_so_the_browser_falls_back(client, monkeypatch):
    async def _no_secret(self, scope, key, expires_in_seconds=900, *, internal=False, response_headers=None):
        raise PresignOverridesUnavailable("no secret")

    monkeypatch.setattr(Storage, "presigned_get_url", _no_secret)
    _put(client, "models/a.glb", b"glTF....")
    r = _download_url(client, {"key": "models/a.glb", "attachment": True})
    assert r.status_code == 503


def test_missing_object_is_404(client):
    assert _download_url(client, {"key": "models/none.glb", "attachment": True}).status_code == 404


# --- against a real S3 endpoint ----------------------------------------------------------------
# As test_storage_put_path.py: runs when ADA_TEST_S3_* point at a store, skips otherwise.
_S3_ENV = ("ADA_TEST_S3_ENDPOINT", "ADA_TEST_S3_BUCKET", "ADA_TEST_S3_ACCESS_KEY", "ADA_TEST_S3_SECRET_KEY")
s3_only = pytest.mark.skipif(
    not all(os.environ.get(k) for k in _S3_ENV), reason="no S3 endpoint configured (ADA_TEST_S3_*)"
)


@s3_only
def test_s3_presigned_attachment_serves_the_overrides():
    endpoint = os.environ["ADA_TEST_S3_ENDPOINT"]
    secret = os.environ["ADA_TEST_S3_SECRET_KEY"]
    store = S3Store(
        os.environ["ADA_TEST_S3_BUCKET"],
        endpoint=endpoint,
        region=os.environ.get("ADA_TEST_S3_REGION", "garage"),
        access_key_id=os.environ["ADA_TEST_S3_ACCESS_KEY"],
        secret_access_key=secret,
        allow_http=endpoint.lower().startswith("http://"),
        virtual_hosted_style_request=False,
    )
    prefix = f"_test/presigned-download-{uuid.uuid4().hex[:12]}"
    storage = Storage(store, prefix=prefix, presign_secret=secret)
    scope = Scope.shared()
    plain = os.urandom(4096)
    text = b"ISO-10303-21;\n" * 500
    name = "ø model (1).ifc"

    def _get(url: str) -> tuple[bytes, dict]:
        # urllib does not decode Content-Encoding: the raw bytes are what the store sent.
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read(), {k.lower(): v for k, v in resp.headers.items()}

    async def _main():
        try:
            await storage.put_bytes(scope, "a/plain.bin", plain)
            await storage.put_bytes(scope, "a/text.ifc", text, content_encoding="gzip")
            # A gzip-at-rest object the store holds no encoding for (a client that compressed
            # without labelling it): the override is what makes the browser decode it.
            await obs.put_async(store, storage._full_key(scope, "a/unlabelled.ifc"), gzip.compress(text))
            disposition = content_disposition(name)
            urls = {}
            for key, enc in (("a/plain.bin", None), ("a/text.ifc", "gzip"), ("a/unlabelled.ifc", "gzip")):
                headers = {"Content-Disposition": disposition, "Content-Type": "application/octet-stream"}
                if enc:
                    headers["Content-Encoding"] = enc
                urls[key] = await storage.presigned_get_url(scope, key, response_headers=headers)
            return disposition, urls
        except BaseException:
            await _cleanup()
            raise

    async def _cleanup():
        async for page in obs.list(store, prefix=prefix):
            for meta in page:
                await store.delete_async(meta["path"])

    disposition, urls = asyncio.run(_main())
    try:
        body, headers = _get(urls["a/plain.bin"])
        assert body == plain
        assert headers["content-disposition"] == disposition
        assert headers["content-type"] == "application/octet-stream"
        assert "content-encoding" not in headers
        for key in ("a/text.ifc", "a/unlabelled.ifc"):
            body, headers = _get(urls[key])
            assert headers["content-disposition"] == disposition
            assert headers["content-encoding"] == "gzip"
            assert gzip.decompress(body) == text
    finally:
        asyncio.run(_cleanup())
