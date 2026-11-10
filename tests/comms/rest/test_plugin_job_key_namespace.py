"""Where a plugin job may write: its summary key and its prefix stay in the plugin's own namespace.

``POST /api/plugins/{id}/jobs`` lets a caller name the job's ``derived_key`` (a plugin that keys
its own cache, as a detailing plugin does) and ``derived_prefix``. Both are now accepted only under
``_derived/plugin_jobs/<id>/`` or ``_derived/<id>/``; a body naming anything else is refused with
a 400 rather than quietly replaced, so a caller that got its key wrong finds out.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("ADA_VIEWER_STORAGE_KIND", "local")
os.environ.setdefault("ADA_VIEWER_LOCAL_PATH", tempfile.mkdtemp(prefix="ada-test-storage-"))

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ada.comms.rest.app import create_app
from ada.comms.rest.routes.plugin_jobs import plugin_owned_key
from ada.plugins import register_plugin_backend, reset_registry

from .test_plugin_job_admin_gate import _settings

PLUGIN = "adapy-test-keys"


def _entrypoint(options, *, storage, scope, on_progress, derived_prefix, **_kw):
    return {"ok": True}


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    register_plugin_backend(PLUGIN, job_entrypoint=f"{__name__}:_entrypoint")
    yield
    reset_registry()


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(_settings(tmp_path)), raise_server_exceptions=False) as c:
        yield c


# --- the rule ---------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        f"_derived/plugin_jobs/{PLUGIN}/abc.json",
        f"_derived/{PLUGIN}/summary/model__p1.json",
    ],
)
def test_a_key_in_the_plugins_namespace_is_accepted(key):
    assert plugin_owned_key(PLUGIN, key, "derived_key") == key


@pytest.mark.parametrize("value", [None, "", "   "])
def test_nothing_sent_means_the_default(value):
    assert plugin_owned_key(PLUGIN, value, "derived_key") is None


@pytest.mark.parametrize(
    "key",
    [
        "models/plant.ifc",  # not under _derived/ at all
        "_derived/models/plant.ifc.glb",  # another source's derived product
        "_derived/plugin_jobs/another-plugin/abc.json",  # another plugin's
        "_derived/another-plugin/abc.json",
        f"_derived/plugin_jobs/{PLUGIN}/../another-plugin/abc.json",
        f"_derived/{PLUGIN}/./abc.json",
        f"_derived/{PLUGIN}//abc.json",
        f"_derived/{PLUGIN}/",  # a key needs a name
        f"/_derived/{PLUGIN}/abc.json",
        f"_derived\\{PLUGIN}\\abc.json",
        f"_derived/{PLUGIN}-other/abc.json",  # a longer id is not this one
    ],
)
def test_a_key_outside_it_is_refused(key):
    with pytest.raises(HTTPException) as e:
        plugin_owned_key(PLUGIN, key, "derived_key")
    assert e.value.status_code == 400
    assert f"_derived/plugin_jobs/{PLUGIN}/" in e.value.detail


def test_a_key_that_is_not_a_string_is_refused():
    with pytest.raises(HTTPException) as e:
        plugin_owned_key(PLUGIN, ["_derived/x"], "derived_key")
    assert e.value.status_code == 400


@pytest.mark.parametrize(
    "prefix", [f"_derived/{PLUGIN}", f"_derived/{PLUGIN}/", f"_derived/plugin_jobs/{PLUGIN}/run-1/"]
)
def test_a_prefix_may_be_the_namespace_itself_or_end_in_a_slash(prefix):
    assert plugin_owned_key(PLUGIN, prefix, "derived_prefix", prefix=True) == prefix


@pytest.mark.parametrize("prefix", ["_derived/", "_derived/models/plant.ifc.fea/", f"_derived/{PLUGIN}/../"])
def test_a_prefix_outside_it_is_refused(prefix):
    with pytest.raises(HTTPException):
        plugin_owned_key(PLUGIN, prefix, "derived_prefix", prefix=True)


def test_a_plugin_named_plugin_jobs_does_not_own_every_plugins_outputs():
    with pytest.raises(HTTPException):
        plugin_owned_key("plugin_jobs", "_derived/plugin_jobs/another-plugin/abc.json", "derived_key")


# --- the route --------------------------------------------------------------


def test_the_route_answers_the_key_it_was_asked_for_when_it_is_the_plugins(client):
    key = f"_derived/{PLUGIN}/summary/model__p1.json"
    r = client.post(f"/api/plugins/{PLUGIN}/jobs", json={"options": {}, "derived_key": key})
    assert r.status_code == 200, r.text
    assert r.json()["derived_key"] == key


def test_the_route_still_defaults_the_key(client):
    r = client.post(f"/api/plugins/{PLUGIN}/jobs", json={"options": {}})
    assert r.status_code == 200, r.text
    assert r.json()["derived_key"].startswith(f"_derived/plugin_jobs/{PLUGIN}/")


@pytest.mark.parametrize(
    "body",
    [
        {"options": {}, "derived_key": "_derived/models/plant.ifc.glb"},
        {"options": {}, "derived_key": "models/plant.ifc"},
        {"options": {}, "derived_prefix": "_derived/models/plant.ifc.fea/"},
    ],
)
def test_the_route_refuses_a_key_or_prefix_outside_the_namespace(client, body):
    r = client.post(f"/api/plugins/{PLUGIN}/jobs", json=body)
    assert r.status_code == 400, r.text
