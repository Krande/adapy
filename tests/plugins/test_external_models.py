"""Contract tests for the built-in external-models plugin."""

from __future__ import annotations

import importlib

import pytest

from ada.plugins import plugin_backend_spec, reset_registry
from ada.plugins.external_models import (
    DEMO_PROVIDER_ID,
    PLUGIN_ID,
    external_model_providers,
    get_external_model_provider,
    register,
    register_external_model_provider,
    reset_providers,
)
from ada.plugins.external_models.adapy_plugin import run_job
from ada.plugins.external_models.catalog import (
    LABELS_FILENAME,
    Collection,
    ExternalModel,
    ExternalModelCatalog,
    ModelRevision,
    S3ExternalModelCatalog,
    StubExternalModelCatalog,
    demo_catalog_from_env,
)

EXTERNAL_ENV = (
    "ADA_EXTERNAL_MODELS_CATALOG",
    "ADA_EXTERNAL_MODELS_S3_BUCKET",
    "ADA_EXTERNAL_MODELS_S3_ENDPOINT",
    "ADA_EXTERNAL_MODELS_S3_ENDPOINT_PUBLIC",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in EXTERNAL_ENV:
        monkeypatch.delenv(name, raising=False)
    reset_providers()
    yield
    reset_providers()


# --- the seam ---------------------------------------------------------------


def test_stub_satisfies_the_protocol():
    assert isinstance(StubExternalModelCatalog(), ExternalModelCatalog)


def test_s3_class_satisfies_the_protocol():
    # Structural check only — no instance, so no bucket and no network.
    assert issubclass(S3ExternalModelCatalog, ExternalModelCatalog) or all(
        hasattr(S3ExternalModelCatalog, m) for m in ("list_collections", "list_models", "model_download_url")
    )


def test_stub_lists_collections_marked_as_stub():
    cols = StubExternalModelCatalog().list_collections()
    assert [c.id for c in cols] == ["demo", "samples"]
    # A misconfigured deployment must be visible, not silently empty.
    assert all(c.name.endswith("(stub)") for c in cols)


def test_stub_lists_models_of_a_collection():
    models = StubExternalModelCatalog().list_models("demo")
    assert [m.id for m in models] == ["kitchen", "pipe-rack"]
    assert all(isinstance(m, ExternalModel) for m in models)
    assert models[0].key == "demo/kitchen.glb"


def test_stub_unknown_collection_is_empty_not_an_error():
    assert StubExternalModelCatalog().list_models("nope") == []


def test_stub_download_url_is_deliberately_unfetchable():
    url = StubExternalModelCatalog().model_download_url("demo", "kitchen")
    # Browsing works under the stub; loading must fail loudly rather than
    # looking like a network blip.
    assert url.startswith("stub://")


# --- catalogue selection ----------------------------------------------------


def test_demo_provider_defaults_to_stub():
    assert isinstance(demo_catalog_from_env(), StubExternalModelCatalog)


def test_demo_provider_s3_without_bucket_raises_rather_than_falling_back(monkeypatch):
    monkeypatch.setenv("ADA_EXTERNAL_MODELS_CATALOG", "s3")
    # Falling back to the stub would hide the misconfiguration.
    with pytest.raises(ValueError, match="ADA_EXTERNAL_MODELS_S3_BUCKET"):
        demo_catalog_from_env()


def test_demo_provider_rejects_unknown_kind(monkeypatch):
    monkeypatch.setenv("ADA_EXTERNAL_MODELS_CATALOG", "azure")
    with pytest.raises(ValueError, match="unknown"):
        demo_catalog_from_env()


# --- the two-level rule -----------------------------------------------------


class _FakeS3(S3ExternalModelCatalog):
    """Exercises the key-walking logic without obstore or a bucket."""

    def __init__(self, keys):
        self._keys = keys

    def _list_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._keys if k.startswith(prefix)]


def test_s3_collections_are_top_level_prefixes_only():
    cat = _FakeS3(["demo/a.glb", "demo/b.glb", "plant-a/c.glb", "loose.glb"])
    # `loose.glb` sits at the root and belongs to no collection.
    assert [c.id for c in cat.list_collections()] == ["demo", "plant-a"]


def test_s3_models_ignore_deeper_nesting_and_non_models():
    cat = _FakeS3(["demo/a.glb", "demo/nested/b.glb", "demo/README.md", "demo/c.gltf"])
    assert [m.id for m in cat.list_models("demo")] == ["a", "c"]


# --- the job contract -------------------------------------------------------


def test_run_job_rejects_unknown_action():
    with pytest.raises(ValueError, match="unknown action"):
        run_job({"action": "drop_table"})


def test_run_job_lists_collections():
    out = run_job({"action": "list_collections"}, catalog=StubExternalModelCatalog())
    assert out["action"] == "list_collections"
    assert [c["id"] for c in out["collections"]] == ["demo", "samples"]


def test_run_job_list_models_requires_a_collection():
    with pytest.raises(ValueError, match="collection"):
        run_job({"action": "list_models"}, catalog=StubExternalModelCatalog())


def test_run_job_model_url_requires_a_model_id():
    with pytest.raises(ValueError, match="model_id"):
        run_job({"action": "model_url", "collection": "demo"}, catalog=StubExternalModelCatalog())


def test_run_job_payload_is_json_serialisable():
    import json

    out = run_job({"action": "list_models", "collection": "demo"}, catalog=StubExternalModelCatalog())
    json.loads(json.dumps(out))  # must not raise


def test_run_job_ignores_viewer_storage_and_scope():
    """storage/scope are bound to the VIEWER's bucket; using them would defeat
    the point of a separate catalogue."""

    class _Boom:
        def __getattr__(self, name):
            raise AssertionError(f"the plugin must not touch viewer storage (accessed {name!r})")

    out = run_job(
        {"action": "list_collections"},
        storage=_Boom(),
        scope=_Boom(),
        catalog=StubExternalModelCatalog(),
    )
    assert out["collections"]


def test_progress_failure_never_sinks_the_job():
    def _bad(stage, frac):
        raise RuntimeError("progress backend down")

    out = run_job({"action": "list_collections"}, on_progress=_bad, catalog=StubExternalModelCatalog())
    assert out["collections"]


# --- registration -----------------------------------------------------------


def test_register_advertises_the_spec():
    reset_registry()
    try:
        register()
        spec = plugin_backend_spec(PLUGIN_ID)
        assert spec is not None
        assert spec["worker_capability"] == "external-models"
        assert spec["slug"] == PLUGIN_ID
    finally:
        reset_registry()


def test_advertised_entrypoint_actually_resolves_to_run_job():
    """The worker resolves 'module:callable' from the spec. A typo here is only
    discoverable at job time, in production."""
    reset_registry()
    try:
        register()
        entry = plugin_backend_spec(PLUGIN_ID)["job_entrypoint"]
        mod_name, _, attr = entry.partition(":")
        fn = getattr(importlib.import_module(mod_name), attr)
        assert fn is run_job
    finally:
        reset_registry()


def test_importing_the_package_does_not_auto_register():
    reset_registry()
    importlib.reload(importlib.import_module("ada.plugins.external_models"))
    assert plugin_backend_spec(PLUGIN_ID) is None


# --- the provider registry (the wrapper half) -------------------------------


def test_register_installs_the_demo_provider():
    register()
    assert [p["id"] for p in external_model_providers()] == [DEMO_PROVIDER_ID]
    assert isinstance(get_external_model_provider(DEMO_PROVIDER_ID), StubExternalModelCatalog)


def test_unknown_provider_error_names_what_is_registered():
    register()
    with pytest.raises(KeyError, match="demo"):
        get_external_model_provider("not-installed")


def test_factory_is_lazy_and_called_once():
    """Registering must not construct a client — an S3 provider would read
    credentials and open a connection at import time."""
    calls = []

    def _factory():
        calls.append(1)
        return StubExternalModelCatalog()

    register_external_model_provider("lazy", _factory)
    assert calls == []
    get_external_model_provider("lazy")
    get_external_model_provider("lazy")
    assert calls == [1]


def test_replacing_a_provider_drops_its_cached_instance():
    first, second = StubExternalModelCatalog(), StubExternalModelCatalog()
    register_external_model_provider("p", lambda: first)
    assert get_external_model_provider("p") is first
    register_external_model_provider("p", lambda: second)
    assert get_external_model_provider("p") is second


def test_a_third_party_provider_registers_exactly_like_the_builtin():
    """An out-of-tree plugin plugs in through the same public call with no core
    change. This is the property the whole abstraction exists for."""

    class _ThirdPartyCatalog:
        def list_collections(self):
            return [Collection(id="alpha", name="Alpha")]

        def list_models(self, collection):
            return [ExternalModel(id="unit-1", name="unit-1", collection=collection, key="alpha/unit-1.glb")]

        def model_download_url(self, collection, model_id, *, expires_in_seconds=900):
            return "https://example.invalid/unit-1.glb"

    register()
    register_external_model_provider("third-party", _ThirdPartyCatalog, label="Third party")
    assert {p["id"] for p in external_model_providers()} == {DEMO_PROVIDER_ID, "third-party"}

    # The same call shape serves both — the consumer only varies the id.
    for pid, expected in ((DEMO_PROVIDER_ID, "demo"), ("third-party", "alpha")):
        out = run_job({"action": "list_collections", "provider": pid})
        assert out["provider"] == pid
        assert expected in [c["id"] for c in out["collections"]]


def test_run_job_lists_providers():
    register()
    out = run_job({"action": "list_providers"})
    assert out["providers"] == [{"id": "demo", "label": "Demo (object store)"}]


def test_run_job_defaults_to_the_demo_provider():
    register()
    out = run_job({"action": "list_collections"})
    assert out["provider"] == DEMO_PROVIDER_ID


# --- label manifest -----------------------------------------------------------


class _LabelledS3(S3ExternalModelCatalog):
    """Exercises the label path without obstore or a bucket."""

    def __init__(self, keys, labels=None):
        self._keys = keys
        self._labels_data = labels

    def _list_keys(self, prefix: str = "") -> list[str]:
        return [k for k in self._keys if k.startswith(prefix)]

    def _labels(self, collection: str):
        return self._labels_data or {}


def test_label_from_the_manifest_replaces_the_displayed_name():
    cat = _LabelledS3(["plant/$X100-PIPE.glb"], {"$X100-PIPE": "X100 Piping"})
    m = cat.list_models("plant")[0]
    assert m.name == "X100 Piping"
    assert m.labelled is True
    # The id must NOT move: it addresses the object, so a renamed label would
    # otherwise break every existing binding and load.
    assert m.id == "$X100-PIPE"
    assert m.key == "plant/$X100-PIPE.glb"


def test_unlabelled_model_falls_back_to_its_filename():
    cat = _LabelledS3(["plant/$X100-PIPE.glb"], {})
    m = cat.list_models("plant")[0]
    assert m.name == "$X100-PIPE"
    assert m.labelled is False


def test_partial_manifest_labels_only_what_it_names():
    cat = _LabelledS3(
        ["plant/a.glb", "plant/b.glb"],
        {"a": "Alpha"},
    )
    got = {m.id: (m.name, m.labelled) for m in cat.list_models("plant")}
    assert got == {"a": ("Alpha", True), "b": ("b", False)}


def test_the_manifest_is_not_itself_listed_as_a_model():
    # It lives beside the models and is not one; listing it would offer an
    # unloadable entry named after the file.
    cat = _LabelledS3([f"plant/{LABELS_FILENAME}", "plant/a.glb"], {})
    assert [m.id for m in cat.list_models("plant")] == ["a"]


# --- upload: a provider opts in by implementing it --------------------------


def test_a_read_only_provider_offers_no_upload_and_says_which():
    """The stub publishes nothing, so it implements nothing.

    The refusal names the provider rather than saying "unsupported", because
    the useful fact is WHICH catalogue declined.
    """
    cat = StubExternalModelCatalog()
    out = run_job({"action": "list_models", "collection": "demo"}, catalog=cat)
    assert out["can_upload"] is False
    with pytest.raises(ValueError, match="does not accept uploads"):
        run_job(
            {"action": "model_upload_url", "collection": "demo", "model_id": "x.glb"},
            catalog=cat,
        )


def test_an_object_store_declares_upload_by_implementing_it():
    # Asked through `list_collections`, which the fake answers from its own key
    # list — `list_models` reaches for the labels object and so wants obstore,
    # which this fixture deliberately does without.
    cat = _FakeS3(["demo/a.glb"])
    out = run_job({"action": "list_collections"}, catalog=cat)
    assert out["can_upload"] is True


def test_the_upload_key_is_derived_never_taken_from_the_caller():
    """A presigned URL grants exactly the write it names.

    So the key is built from (collection, model_id) here. Accepting one would
    let a request choose where its bytes land — over another collection's
    model, or outside the two-level layout this catalogue promises.
    """
    cat = _FakeS3([])
    assert cat._upload_key("plant-a", "unit-a.glb") == "plant-a/unit-a.glb"
    for bad in ["../escape.glb", "nested/deep.glb", ".hidden.glb", "", "notamodel.txt"]:
        with pytest.raises(ValueError):
            cat._upload_key("plant-a", bad)
    with pytest.raises(ValueError, match="one path segment"):
        cat._upload_key("plant-a/sneaky", "a.glb")


def test_the_store_asks_for_gzip_and_a_type_together():
    """The pair is inseparable on purpose.

    Gzipped bytes stored without `Content-Encoding` reach the viewer as gzip
    where it expects glTF and fail as a JSON parse error naming neither the
    file nor compression. This catalogue was found with exactly one object in
    that state out of forty, so an uploader that obeys these headers cannot
    store one without the other.
    """
    cat = _FakeS3([])
    glb = cat.model_upload_headers("plant-a", "unit-a.glb")
    assert glb["Content-Encoding"] == "gzip"
    assert glb["Content-Type"] == "model/gltf-binary"
    assert cat.model_upload_headers("plant-a", "scene.gltf")["Content-Type"] == "model/gltf+json"


# --- revisions: a provider opts in by implementing it -----------------------


def test_a_single_version_provider_declares_no_revisions():
    """Nothing to configure and nothing to raise.

    A catalogue that keeps one version of a model implements neither method, so
    `has_revisions` is False and the action still answers — with an empty list,
    which is the same shape a versioned provider returns for an unversioned
    model. A caller therefore needs no branch between the two cases.
    """
    cat = _FakeS3(["demo/a.glb"])
    out = run_job({"action": "list_collections"}, catalog=cat)
    assert out["has_revisions"] is False

    out = run_job(
        {"action": "list_model_revisions", "collection": "demo", "model_id": "a"},
        catalog=cat,
    )
    assert out["has_revisions"] is False
    assert out["revisions"] == []


def test_a_versioned_provider_lists_its_revisions_newest_first():
    cat = StubExternalModelCatalog()
    out = run_job({"action": "list_models", "collection": "demo"}, catalog=cat)
    assert out["has_revisions"] is True

    out = run_job(
        {"action": "list_model_revisions", "collection": "demo", "model_id": "kitchen"},
        catalog=cat,
    )
    assert [r["id"] for r in out["revisions"]] == ["2024-01-02", "2024-01-01"]
    # Exactly one is current, and it is the one a plain fetch resolves to.
    assert [r["current"] for r in out["revisions"]] == [True, False]


def test_an_unversioned_model_in_a_versioned_provider_is_empty_not_an_error():
    cat = StubExternalModelCatalog()
    out = run_job(
        {"action": "list_model_revisions", "collection": "demo", "model_id": "pipe-rack"},
        catalog=cat,
    )
    assert out["has_revisions"] is True
    assert out["revisions"] == []


def test_a_revision_is_only_passed_when_one_was_asked_for():
    """The kwarg must not reach a provider that predates revisions.

    This is what lets the feature be additive: `model_download_url` keeps its
    original signature for every existing provider, and only a caller holding a
    revision id causes the keyword to be sent at all.
    """

    class _Old:
        def list_collections(self):
            return [Collection(id="c", name="c")]

        def list_models(self, collection):
            return [ExternalModel(id="m", name="m", collection=collection, key="c/m.glb")]

        def model_download_url(self, collection, model_id, *, expires_in_seconds=900):
            # No `revision` parameter at all. Passing one would raise TypeError,
            # so this test failing is exactly the regression to catch.
            return "old://c/m"

    out = run_job({"action": "model_url", "collection": "c", "model_id": "m"}, catalog=_Old())
    assert out["url"] == "old://c/m"
    assert out["revision"] is None


def test_a_requested_revision_reaches_the_provider():
    cat = StubExternalModelCatalog()
    out = run_job(
        {
            "action": "model_url",
            "collection": "demo",
            "model_id": "kitchen",
            "revision": "2024-01-01",
        },
        catalog=cat,
    )
    assert out["revision"] == "2024-01-01"
    assert out["url"].endswith("@2024-01-01")
    # And without one, the same call resolves to whatever the provider calls
    # current -- the pre-existing behaviour, unchanged.
    plain = run_job({"action": "model_url", "collection": "demo", "model_id": "kitchen"}, catalog=cat)
    assert plain["url"] == "stub://external-models/demo/kitchen"


def test_revision_payload_is_json_serialisable():
    import json

    cat = StubExternalModelCatalog()
    out = run_job(
        {"action": "list_model_revisions", "collection": "demo", "model_id": "kitchen"},
        catalog=cat,
    )
    json.dumps(out)
    assert set(out["revisions"][0]) == {"id", "name", "created_at", "size", "current"}


def test_model_revision_is_exported_from_the_package():
    # A provider implementing the optional half imports this from the package
    # root, like Collection and ExternalModel.
    import ada.plugins.external_models as pkg

    assert pkg.ModelRevision is ModelRevision
