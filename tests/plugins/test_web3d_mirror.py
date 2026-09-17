"""The web3d mirror: what it caches, what it re-fetches, and what it refuses.

NO NETWORK ANYWHERE HERE. The blob client and the cache are both fakes, because
the things worth pinning are the DECISIONS -- when a sync transfers and when it
skips, what a status read says about a freshness it did not check, which of
three refusals a caller gets -- and none of those need Azure or a bucket. What
does need them is whether the URLs are right, and that is the half a test cannot
answer anyway.
"""

from __future__ import annotations

import gzip
import json

import pytest

from ada.plugins.external_models import web3d
from ada.plugins.external_models.adapy_plugin import run_job


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClient:
    """A web3d storage account, as three dicts.

    `heads` counts HEADs per blob so a test can show that an unchanged project
    costs one per site and no GETs -- which is the entire claim that this is a
    cache rather than a copy.
    """

    def __init__(self, blobs: dict[str, bytes], etags: dict[str, str]):
        self.blobs = blobs
        self.etags = etags
        self.heads: dict[str, int] = {}
        self.gets: dict[str, int] = {}

    def _key(self, container: str, path: str) -> str:
        return f"{container}/{path}"

    def etag(self, container: str, path: str) -> str:
        k = self._key(container, path)
        self.heads[k] = self.heads.get(k, 0) + 1
        return self.etags.get(k, "")

    def get(self, container: str, path: str):
        k = self._key(container, path)
        self.gets[k] = self.gets.get(k, 0) + 1
        return self.blobs[k], False, self.etags.get(k, "")

    def get_json(self, container: str, path: str):
        raw = self.blobs[self._key(container, path)]
        return json.loads(raw)


class FakeCache:
    """An external-model store: objects, sidecars, and a PUT that records."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.sidecars: dict[str, dict] = {}
        self.puts: list[str] = []

    def list_models(self, collection: str):
        class _M:
            def __init__(self, ident):
                self.id = ident

        prefix = f"{collection}/"
        return [
            _M(k[len(prefix):].rsplit(".", 1)[0])
            for k in self.objects
            if k.startswith(prefix)
        ]

    def model_upload_url(self, collection, model_id, *, expires_in_seconds=900, content_type=None):
        return f"fake://{collection}/{model_id}"

    def model_upload_headers(self, collection, model_id):
        return {"Content-Type": "model/gltf-binary", "Content-Encoding": "gzip"}

    def get_sidecar(self, collection: str, filename: str) -> dict:
        return dict(self.sidecars.get(f"{collection}/{filename}") or {})

    def put_sidecar(self, collection: str, filename: str, data: dict) -> None:
        self.sidecars[f"{collection}/{filename}"] = dict(data)


PROJECT_LIST = json.dumps(
    [
        {"unique_key": "ASP", "container": "asp", "plant_name": "ASP", "config": "project_config_asp.json"},
        {"unique_key": "OLD", "container": "old", "config": "c.json", "active": False},
    ]
).encode()

CONFIG = json.dumps({"model_data": [{"name": "ModelExportMain.rvm", "path": "sites.json"}]}).encode()

SITES = json.dumps(
    {
        "models": {
            "s1": {"name": "/AP400-STRU_MS", "glb_url": "glb/ap400_stru_ms.glb"},
            "s2": {"name": "/AP400-STRU", "glb_url": "glb/ap400_stru.glb"},
            "s3": {"name": "/NO-GEOMETRY", "glb_url": ""},
        }
    }
).encode()


@pytest.fixture
def mirror():
    blobs = {
        "000000/project_list.json": PROJECT_LIST,
        "asp/project_config_asp.json": CONFIG,
        "asp/sites.json": SITES,
        "asp/glb/ap400_stru_ms.glb": b"glTF-one",
        "asp/glb/ap400_stru.glb": b"glTF-two",
    }
    etags = {"asp/glb/ap400_stru_ms.glb": "aaa", "asp/glb/ap400_stru.glb": "bbb"}
    client = FakeClient(blobs, etags)
    cache = FakeCache()
    return web3d.Web3dMirror(web3d.Web3dSource(client), cache, client=client), client, cache


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def test_a_collection_is_one_path_segment():
    assert web3d.collection_for("ASP") == "asp"
    # A slash would let a project key write outside the collection it named,
    # which `_upload_key` refuses anyway -- this is the earlier of the two.
    assert "/" not in web3d.collection_for("a/b")


def test_a_model_id_carries_its_model_file():
    # Two model files publish the same SITE, so a name built from the site
    # alone silently mirrors one over the other.
    a = web3d.model_id_for("ModelExportMain.rvm", "/AP400-STRU_MS")
    b = web3d.model_id_for("ModelExportTempSteel.rvm", "/AP400-STRU_MS")
    assert a != b
    assert a.endswith(".glb")


# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def test_sites_come_from_the_config_blob_named_by_the_project_row(mirror):
    m, _, _ = mirror
    sites = m._source.list_sites("ASP")
    # Three entries in the file; the one with no glb_url is not loadable.
    assert [s.site for s in sites] == ["/AP400-STRU_MS", "/AP400-STRU"]
    assert all(s.container == "asp" for s in sites)


def test_an_inactive_project_is_not_offered(mirror):
    m, _, _ = mirror
    assert [p["key"] for p in m._source.list_projects()] == ["ASP"]
    with pytest.raises(KeyError):
        m._source.project("OLD")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_an_empty_cache_reports_everything_missing_and_stale(mirror):
    m, _, _ = mirror
    report = m.status("ASP")
    assert len(report.entries) == 2
    assert report.cached_count == 0
    assert report.stale_count == 2
    assert all(e.stale is True for e in report.entries)


def test_skipping_the_upstream_check_reports_unknown_rather_than_fresh(mirror):
    m, client, _ = mirror
    report = m.status("ASP", check_source=False)
    # THE POINT: "we did not look" must not render as "up to date".
    assert report.unknown_count == 2
    assert report.stale_count == 0
    assert all(e.stale is None for e in report.entries)
    assert client.heads == {}, "and it cost nothing upstream"


def test_a_head_that_fails_is_recorded_rather_than_read_as_fresh(mirror, monkeypatch):
    m, client, _ = mirror

    def boom(container, path):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(client, "etag", boom)
    report = m.status("ASP")
    assert len(report.failed) == 2
    assert all(e.stale is None for e in report.entries)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def _fake_put(monkeypatch, cache: FakeCache):
    """Capture the PUT the mirror makes, without a socket."""
    import urllib.request

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        cache.objects[req.full_url.replace("fake://", "")] = req.data
        cache.puts.append(req.full_url)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_a_first_sync_transfers_everything_and_records_where_it_came_from(mirror, monkeypatch):
    m, client, cache = mirror
    _fake_put(monkeypatch, cache)

    report = m.sync("ASP")
    assert len(report.transferred) == 2
    assert not report.failed
    note = cache.sidecars["asp/" + web3d.WEB3D_SIDECAR_FILENAME]
    one = note["ModelExportMain.rvm~AP400-STRU_MS.glb"]
    assert one["etag"] == "aaa"
    assert one["container"] == "asp"
    assert one["site"] == "/AP400-STRU_MS"
    assert one["mirrored_at"]


def test_a_second_sync_of_an_unchanged_project_costs_one_head_and_no_bytes(mirror, monkeypatch):
    m, client, cache = mirror
    _fake_put(monkeypatch, cache)
    m.sync("ASP")
    gets_after_first = dict(client.gets)

    again = m.sync("ASP")
    assert again.transferred == [], "nothing changed, so nothing moved"
    assert client.gets == gets_after_first, "and not a byte was re-fetched"
    assert client.heads["asp/glb/ap400_stru_ms.glb"] == 2, "one HEAD per sync, per site"


def test_a_changed_etag_upstream_is_picked_up(mirror, monkeypatch):
    m, client, cache = mirror
    _fake_put(monkeypatch, cache)
    m.sync("ASP")

    client.etags["asp/glb/ap400_stru_ms.glb"] = "aaa2"
    client.blobs["asp/glb/ap400_stru_ms.glb"] = b"glTF-one-rebuilt"

    report = m.sync("ASP")
    assert report.transferred == ["ModelExportMain.rvm~AP400-STRU_MS.glb"]
    note = cache.sidecars["asp/" + web3d.WEB3D_SIDECAR_FILENAME]
    assert note["ModelExportMain.rvm~AP400-STRU_MS.glb"]["etag"] == "aaa2"


def test_force_re_transfers_what_the_etag_says_is_current(mirror, monkeypatch):
    m, _, cache = mirror
    _fake_put(monkeypatch, cache)
    m.sync("ASP")
    assert m.sync("ASP", force=True).transferred


def test_a_dry_run_says_what_would_move_and_writes_nothing(mirror, monkeypatch):
    m, _, cache = mirror
    _fake_put(monkeypatch, cache)
    report = m.sync("ASP", dry_run=True)
    assert len(report.transferred) == 2
    assert report.dry_run
    assert cache.puts == []
    assert cache.sidecars == {}


def test_an_object_deleted_from_the_store_is_re_fetched_despite_the_sidecar(mirror, monkeypatch):
    m, _, cache = mirror
    _fake_put(monkeypatch, cache)
    m.sync("ASP")

    # The sidecar still claims it, but the bucket no longer has it. The
    # sidecar is a record, not the truth.
    cache.objects.pop("asp/ModelExportMain.rvm~AP400-STRU_MS.glb")
    report = m.sync("ASP")
    assert report.transferred == ["ModelExportMain.rvm~AP400-STRU_MS.glb"]


def test_bytes_are_gzipped_on_the_way_in_when_the_cache_asks_for_it(mirror, monkeypatch):
    m, _, cache = mirror
    _fake_put(monkeypatch, cache)
    m.sync("ASP")
    stored = cache.objects["asp/ModelExportMain.rvm~AP400-STRU_MS.glb"]
    # The cache's headers declare Content-Encoding: gzip, and storing raw bytes
    # under that header reaches the viewer as a parse error naming neither the
    # compression nor the file.
    assert gzip.decompress(stored) == b"glTF-one"


# ---------------------------------------------------------------------------
# The three refusals
# ---------------------------------------------------------------------------


def test_a_provider_with_no_upload_surface_is_refused_by_name():
    with pytest.raises(ValueError, match="cannot hold a mirror"):
        run_job({"action": "mirror_status", "provider": "demo"}, catalog=object())


def test_a_deployment_with_no_credential_says_which_variables_are_missing(monkeypatch):
    for var in (web3d.TENANT_VAR, web3d.CLIENT_ID_VAR, web3d.CLIENT_SECRET_VAR):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="no web3d credential"):
        run_job({"action": "mirror_status", "provider": "demo"}, catalog=FakeCache())


def test_a_sync_refuses_while_mirroring_is_switched_off(monkeypatch):
    for var, val in (
        (web3d.TENANT_VAR, "t"),
        (web3d.CLIENT_ID_VAR, "c"),
        (web3d.CLIENT_SECRET_VAR, "s"),
        (web3d.PROJECTS_VAR, "ASP"),
    ):
        monkeypatch.setenv(var, val)
    monkeypatch.setenv(web3d.MIRROR_ENABLED_VAR, "false")
    monkeypatch.delenv(web3d.API_BASE_VAR, raising=False)

    with pytest.raises(ValueError, match="switched off"):
        run_job({"action": "mirror_sync", "provider": "demo"}, catalog=FakeCache())


def test_a_status_read_still_works_while_mirroring_is_switched_off(monkeypatch, mirror):
    # An admin who has just switched the cache off still wants to see what is
    # in it. Only the WRITE refuses.
    m, _, cache = mirror
    for var, val in (
        (web3d.TENANT_VAR, "t"),
        (web3d.CLIENT_ID_VAR, "c"),
        (web3d.CLIENT_SECRET_VAR, "s"),
        (web3d.PROJECTS_VAR, "ASP"),
    ):
        monkeypatch.setenv(var, val)
    monkeypatch.setenv(web3d.MIRROR_ENABLED_VAR, "false")
    monkeypatch.delenv(web3d.API_BASE_VAR, raising=False)
    monkeypatch.setattr(web3d, "mirror_from_env", lambda cache=None: m)

    out = run_job({"action": "mirror_status", "provider": "demo"}, catalog=cache)
    assert out["enabled"] is False
    assert out["projects"]["ASP"]["total"] == 2


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def test_the_setting_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv(web3d.MIRROR_ENABLED_VAR, "true")
    monkeypatch.setattr(web3d, "read_mirror_setting", lambda: {"enabled": False})
    assert web3d.mirror_enabled() is False


def test_an_unreadable_setting_falls_back_to_the_environment(monkeypatch):
    # None means "could not be consulted", which is a third answer and not a
    # default -- a deployment with no API configured must still respect its own
    # environment.
    monkeypatch.setenv(web3d.MIRROR_ENABLED_VAR, "true")
    monkeypatch.setattr(web3d, "read_mirror_setting", lambda: None)
    assert web3d.mirror_enabled() is True


def test_projects_come_from_the_setting_first(monkeypatch):
    monkeypatch.setenv(web3d.PROJECTS_VAR, "FROM_ENV")
    monkeypatch.setattr(web3d, "read_mirror_setting", lambda: {"projects": ["A", "B", "A"]})
    assert web3d.configured_projects() == ["A", "B"]


def test_can_mirror_needs_every_piece_of_the_surface():
    from ada.plugins.external_models.adapy_plugin import _can_mirror

    assert _can_mirror(FakeCache())

    class NoSidecar(FakeCache):
        put_sidecar = None

    assert not _can_mirror(NoSidecar())
