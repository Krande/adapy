"""Uploading a browser-materialised load combination into the server's case cache
(``POST /fea/artefact?name=cases/<n>-<hash8>/fea.*``)."""

from __future__ import annotations

import json
import os
import struct
import tempfile
import uuid

import pytest

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

from fastapi.testclient import TestClient  # noqa: E402

from ada.comms.rest.app import create_app  # noqa: E402
from ada.comms.rest.converter import EXPECTED_FEA_BAKE_VERSION  # noqa: E402
from ada.comms.rest.fea_case_upload import (  # noqa: E402
    CaseUploadError,
    parse_case_file_name,
    validate_case_blob,
)

from .test_fea_manifest import _settings, _stage_manifest, _upload  # noqa: E402

HASH = "ab12cd34" + "0" * 56
RAW_HASH = "ffee0011" + "0" * 56
BLOB = "fea.sesam.nodes.displacement.bin"
STRIDE = 8


def _manifest(src: str) -> dict:
    blob = {"url": BLOB, "header_bytes": 1024, "stride_bytes": STRIDE, "dtype": "float32", "byte_order": "little"}
    return {
        "version": 2,
        "bake_version": EXPECTED_FEA_BAKE_VERSION,
        "src": src,
        "mesh": {},
        "fields": [
            {
                "name_canonical": "sesam.nodes.displacement",
                "components": ["X"],
                "blob": blob,
                "steps": [{"i": 0, "value": 1, "label": "1"}, {"i": 1, "value": 2, "label": "2"}],
                "linear_components": ["X"],
            },
            {
                "name_canonical": "props.thickness",
                "category": "property",
                "components": ["TH"],
                "blob": {**blob, "url": "fea.props.thickness.bin"},
                "steps": [{"i": 0, "value": 0, "label": "0"}],
            },
        ],
        "baked_steps": [1, 2],
        "combination_steps": [
            {
                "n": 101,
                "complex": False,
                "terms": [[1, 1.2, 0.0], [2, 1.1, 0.0]],
                "coefficients": [[1.2, 0.0], [1.1, 0.0]],
                "needs_raw": False,
                "recipe_hash": HASH,
            },
            {
                "n": 102,
                "complex": True,
                "terms": [[1, 1.0, 0.5]],
                "coefficients": [[0.8, 0.4]],
                "needs_raw": True,
                "recipe_hash": RAW_HASH,
            },
        ],
        "lazy_cases": {"version": 1, "server": True, "client_tier_a": True, "cases_prefix": "cases/"},
    }


def _afbl(n_steps: int = 1, stride: int = STRIDE, payload: bytes | None = None, magic: bytes = b"AFBL") -> bytes:
    header = json.dumps(
        {
            "name": "sesam.nodes.displacement",
            "n_steps": n_steps,
            "n_points": 2,
            "n_components": 1,
            "dtype": "float32",
            "stride_bytes": stride,
        },
        separators=(",", ":"),
    ).encode()
    prefix = magic + struct.pack("<II", 1, len(header)) + header
    return prefix + b"\0" * (1024 - len(prefix)) + (payload if payload is not None else b"\0" * stride * n_steps)


def _overlay(n: int = 101, recipe: str = HASH, **kw) -> dict:
    doc = {
        "version": 1,
        "kind": "fea_case",
        "bake_version": EXPECTED_FEA_BAKE_VERSION,
        "src": "deck",
        "case": {"n": n, "recipe_hash": recipe},
        "producer": {"engine": "adacpp-wasm", "version": "adacpp_fea/1", "tier": "A"},
        "fields": [
            {
                "name_canonical": "sesam.nodes.displacement",
                "components": ["X"],
                "n_steps": 1,
                "steps": [{"i": 0, "value": 101, "label": "101"}],
                "blob": {"url": BLOB, "header_bytes": 1024, "stride_bytes": STRIDE},
                "scalar_range": {"X": [0.0, 1.0]},
            }
        ],
    }
    doc.update(kw)
    return doc


@pytest.fixture
def client(tmp_path):
    app = create_app(_settings(tmp_path))
    with TestClient(app) as c:
        yield c


def _source(client, tmp_path) -> str:
    src = f"models/{uuid.uuid4().hex}.SIN"
    _upload(client, src, b"sin-bytes")
    _stage_manifest(tmp_path, src, _manifest(src))
    return src


def _post(client, src: str, name: str, body: bytes):
    return client.post("/api/scopes/shared/fea/artefact", params={"source": src, "name": name}, content=body)


def test_an_uploaded_case_is_served_by_the_case_route(client, tmp_path):
    src = _source(client, tmp_path)
    case_dir = f"cases/101-{HASH[:8]}/"
    payload = struct.pack("<2f", 0.5, -1.25)
    r = _post(client, src, case_dir + BLOB, _afbl(payload=payload))
    assert r.status_code == 201, r.text
    assert r.json()["key"] == f"_derived/{src}.fea/{case_dir}{BLOB}"
    sent = _overlay(producer={"engine": "adacpp-wasm", "version": "adacpp_fea/1", "tier": "A", "evil": "x"})
    r = _post(client, src, case_dir + "fea.case.json", json.dumps(sent).encode())
    assert r.status_code == 201, r.text

    r = client.get("/api/scopes/shared/fea/case", params={"key": src, "case": 101})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prefix"] == f"_derived/{src}.fea/{case_dir}"
    assert body["case"]["recipe_hash"] == HASH
    # The server stamps the producer; nothing else the client put there survives.
    assert body["producer"] == {"engine": "adacpp-wasm", "version": "adacpp_fea/1", "tier": "A", "uploaded": True}
    blob = client.get(f"/api/scopes/shared/blobs/_derived/{src}.fea/{case_dir}{BLOB}")
    assert blob.status_code == 200 and blob.content[1024:] == payload

    # Cached now: a second upload does not replace it.
    assert _post(client, src, case_dir + BLOB, _afbl()).status_code == 409


def test_the_overlay_needs_its_blobs_first(client, tmp_path):
    src = _source(client, tmp_path)
    r = _post(client, src, f"cases/101-{HASH[:8]}/fea.case.json", json.dumps(_overlay()).encode())
    assert r.status_code == 409 and "blobs first" in r.json()["detail"]


@pytest.mark.parametrize(
    "name, status",
    [
        (f"cases/7-{HASH[:8]}/{BLOB}", 404),  # not a combination of the bake
        (f"cases/101-deadbeef/{BLOB}", 409),  # a recipe the bake does not have
        (f"cases/102-{RAW_HASH[:8]}/{BLOB}", 422),  # only the raw path can do it
        (f"cases/101-{HASH[:8]}/fea.props.thickness.bin", 400),  # a property field
        (f"cases/101-{HASH[:8]}/fea.other.bin", 400),  # not a blob of the bake
        (f"cases/101-{HASH[:8]}/../fea.manifest.json", 400),  # traversal: not a case name at all
        (f"cases/101-{HASH[:8]}/sub/{BLOB}", 400),
        (f"x/cases/101-{HASH[:8]}/{BLOB}", 400),
    ],
)
def test_case_upload_guards(client, tmp_path, name, status):
    src = _source(client, tmp_path)
    r = _post(client, src, name, _afbl())
    assert r.status_code == status, (name, r.text)


@pytest.mark.parametrize(
    "body",
    [
        _afbl(n_steps=2),  # a base blob, not one step (wrong size)
        _afbl()[:-1],  # short
        _afbl(magic=b"AFXX"),  # not a blob
        _afbl(stride=4, payload=b"\0" * 8),  # header disagrees with the bake
    ],
)
def test_a_case_blob_must_be_one_step_of_the_bakes_layout(client, tmp_path, body):
    src = _source(client, tmp_path)
    r = _post(client, src, f"cases/101-{HASH[:8]}/{BLOB}", body)
    assert r.status_code == 400, r.text


@pytest.mark.parametrize(
    "overlay, status",
    [
        (_overlay(kind="fea_envelope"), 400),
        (_overlay(bake_version=3), 409),
        (_overlay(case={"n": 101, "recipe_hash": "ab12cd34" + "1" * 56}), 409),
        (_overlay(fields=[{"name_canonical": "x", "blob": {"url": "fea.manifest.json"}}]), 400),
        (_overlay(fields=[]), 400),
    ],
)
def test_overlay_guards(client, tmp_path, overlay, status):
    src = _source(client, tmp_path)
    assert _post(client, src, f"cases/101-{HASH[:8]}/{BLOB}", _afbl()).status_code == 201
    r = _post(client, src, f"cases/101-{HASH[:8]}/fea.case.json", json.dumps(overlay).encode())
    assert r.status_code == status, r.text


def test_no_base_bake_no_upload(client, tmp_path):
    bare = f"models/{uuid.uuid4().hex}.SIN"
    _upload(client, bare, b"sin-bytes")
    assert _post(client, bare, f"cases/101-{HASH[:8]}/{BLOB}", _afbl()).status_code == 409


def test_case_file_names():
    cf = parse_case_file_name(f"cases/101-{HASH[:8]}/{BLOB}")
    assert (cf.n, cf.hash8, cf.file, cf.case_dir) == (101, HASH[:8], BLOB, f"101-{HASH[:8]}")
    assert parse_case_file_name(f"cases/101-{HASH[:8]}/fea.case.json").is_overlay
    for bad in ["fea.x.bin", "cases/101/fea.x.bin", "cases/101-ABCDEF12/fea.x.bin", "cases/101-ab12cd34/x.bin", ""]:
        assert parse_case_file_name(bad) is None, bad
    with pytest.raises(CaseUploadError):
        validate_case_blob(_manifest("s"), cf, b"")
