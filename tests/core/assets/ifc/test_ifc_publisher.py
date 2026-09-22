"""``IfcAssetPublisher`` (Phase 4): ``derive()`` PLANS through the core ``AssetPublisher``
protocol, and core (``apply_publish_plan``) writes it -- this is the same derivation
``publish_ifc`` uses (``ada.assets.ifc.publish._derive_ifc_plan``), so the two entry points can
never disagree about what a given staged file means. Pins: the owner gate (Decision 6), leaf
revision > storey revision under-without-stem (Decision 4), re-publish keeps the old revision, and
that ``source_actor`` is relayed ONLY when the IFC file genuinely has more than one owner history
(Decision 6's IFC rule) -- and never ``published_by``.
"""

from __future__ import annotations

import dataclasses
import pathlib

import ifcopenshell
import pytest

from ada.assets.ifc.publish import IFC_PROVIDER_ID, SOURCE_FILENAME
from ada.assets.ifc.publisher import IfcAssetPublisher
from ada.assets.keys import ASSET_PREFIX
from ada.assets.manifest import Actor, ChangeRecord, parse_manifest
from ada.assets.publish import PublishError, apply_publish_plan
from ada.assets.published import PublishedAssetProvider

from .fake_store import FakeStore, FakeSyncStorageFacade

CORPUS_DIR = pathlib.Path(__file__).parents[1] / "corpus"
V1 = CORPUS_DIR / "plant-a_v1.ifc"
V2 = CORPUS_DIR / "plant-a_v2.ifc"
V2_LEAF = CORPUS_DIR / "plant-a_v2-leaf.ifc"

PUBLISHED_BY = Actor(id="u-krisa", display="Kris A.")


def _raw(path: pathlib.Path) -> bytes:
    return path.read_bytes()


def _names(raw: bytes) -> dict[str, str]:
    f = ifcopenshell.file.from_string(raw.decode())
    return {p.Name: p.GlobalId for p in f.by_type("IfcProduct") if p.Name}


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def facade(store: FakeStore) -> FakeSyncStorageFacade:
    return FakeSyncStorageFacade(store)


def _stage(store: FakeStore, raw: bytes, staging_id: str = "x") -> dict[str, str]:
    key = f"assets/_staging/{staging_id}/{SOURCE_FILENAME}"
    store.put_bytes(key, raw)
    return {SOURCE_FILENAME: key}


def _derive_and_apply(facade, staged, *, collection="plant-a", options=None, dry_run=False, replace=False, via="user"):
    publisher = IfcAssetPublisher()
    plan = publisher.derive(None, staged, storage=facade, collection=collection, options=options or {}, dry_run=dry_run)
    occupied = {k for k in facade.list_keys(f"{ASSET_PREFIX}/{collection}/") if any(k == w.key for w in plan.writes)}
    outcome = apply_publish_plan(
        plan,
        published_by=PUBLISHED_BY,
        published_via=via,
        dry_run=dry_run,
        replace_existing=replace,
        occupied=occupied,
        write=lambda key, data: facade.put_bytes(key, data),
    )
    return plan, outcome


# --- whole-file publish, via the provider protocol ----------------------------------------------


def test_derive_then_apply_fans_out_two_sites_one_source_one_index(store, facade):
    staged = _stage(store, _raw(V1))
    plan, outcome = _derive_and_apply(facade, staged, options={"extracted_at": "2026-01-01T00:00:00Z"})

    assert len(outcome.subjects) == 2
    assert plan.subjects == outcome.subjects  # the plan and the outcome agree on what was published

    source_keys = [k for k in outcome.written if k.endswith(f"/{SOURCE_FILENAME}")]
    assert len(source_keys) == 1

    provider = PublishedAssetProvider(store.reader(), provider_id=IFC_PROVIDER_ID)
    for subject in outcome.subjects:
        manifest = provider.manifest("plant-a", subject)
        assert manifest.delivery == "build"
        assert manifest.change.published_by == PUBLISHED_BY
        assert manifest.change.published_via == "user"


def test_apply_publish_plan_stamps_service_via_for_a_scheduled_republish(store, facade):
    staged = _stage(store, _raw(V1))
    _, outcome = _derive_and_apply(facade, staged, options={"extracted_at": "2026-01-01T00:00:00Z"}, via="service")

    provider = PublishedAssetProvider(store.reader(), provider_id=IFC_PROVIDER_ID)
    for subject in outcome.subjects:
        manifest = provider.manifest("plant-a", subject)
        assert manifest.change.published_via == "service"


def test_dry_run_via_provider_writes_nothing(store, facade):
    staged = _stage(store, _raw(V1))
    plan, outcome = _derive_and_apply(facade, staged, options={"extracted_at": "2026-01-01T00:00:00Z"}, dry_run=True)
    assert outcome.dry_run is True
    assert len(outcome.written) == len(plan.writes) > 0
    assert set(store.blobs) == {list(staged.values())[0]}  # only the staged input exists


def test_replace_is_the_only_way_into_an_occupied_revision(store, facade):
    staged = _stage(store, _raw(V1))
    opts = {"extracted_at": "2026-01-01T00:00:00Z"}
    _derive_and_apply(facade, staged, options=opts)

    with pytest.raises(PublishError, match="already exist"):
        _derive_and_apply(facade, staged, options=opts)

    _, outcome = _derive_and_apply(facade, staged, options=opts, replace=True)
    assert outcome.revision == "20260101T000000Z"


# --- re-publish (v2): new revision, old kept -----------------------------------------------------


def test_republish_v2_keeps_v1_revision(store, facade):
    staged_v1 = _stage(store, _raw(V1), "v1")
    _, outcome1 = _derive_and_apply(facade, staged_v1, options={"extracted_at": "2026-01-01T00:00:00Z"})

    staged_v2 = _stage(store, _raw(V2), "v2")
    _, outcome2 = _derive_and_apply(facade, staged_v2, options={"extracted_at": "2026-01-02T00:00:00Z"})

    assert outcome2.revision > outcome1.revision
    assert outcome1.subjects == outcome2.subjects  # same two sites, same guids (Decision-stable corpus)

    idx_key_v1 = f"{ASSET_PREFIX}/plant-a/plant-a/{outcome1.revision}/asset.json"
    idx_key_v2 = f"{ASSET_PREFIX}/plant-a/plant-a/{outcome2.revision}/asset.json"
    assert store.get_bytes(idx_key_v1)  # old kept
    assert store.get_bytes(idx_key_v2)  # new present


# --- owner gate ------------------------------------------------------------------------------------


def test_derived_manifests_never_set_published_by(store, facade):
    """The provider itself never sets it -- the structural half of the owner gate."""
    staged = _stage(store, _raw(V1))
    publisher = IfcAssetPublisher()
    plan = publisher.derive(
        None, staged, storage=facade, collection="plant-a", options={"extracted_at": "2026-01-01T00:00:00Z"}
    )
    for write in plan.writes:
        if write.key.endswith("/asset.json"):
            manifest = parse_manifest(write.data)
            assert manifest.change is None or manifest.change.published_by is None
            assert manifest.change is None or manifest.change.published_via is None


def test_a_manifest_that_sets_published_by_is_refused(store, facade):
    """Core's procedural half of the same gate: if a plan's manifest DID carry published_by
    (simulated here by tampering with one write after deriving), apply_publish_plan refuses it by
    name rather than silently overwriting a lie."""
    staged = _stage(store, _raw(V1))
    publisher = IfcAssetPublisher()
    plan = publisher.derive(
        None, staged, storage=facade, collection="plant-a", options={"extracted_at": "2026-01-01T00:00:00Z"}
    )
    manifest_write = next(w for w in plan.writes if w.key.endswith("/asset.json") and "/plant-a/plant-a/" not in w.key)
    tampered_manifest = parse_manifest(manifest_write.data)
    tampered_manifest = dataclasses.replace(
        tampered_manifest,
        change=dataclasses.replace(tampered_manifest.change or ChangeRecord(), published_by=PUBLISHED_BY),
    )
    tampered_writes = tuple(
        dataclasses.replace(w, data=tampered_manifest.to_json()) if w.key == manifest_write.key else w
        for w in plan.writes
    )
    tampered_plan = dataclasses.replace(plan, writes=tampered_writes)

    with pytest.raises(PublishError, match="published_by"):
        apply_publish_plan(
            tampered_plan,
            published_by=PUBLISHED_BY,
            published_via="user",
            dry_run=False,
            replace_existing=False,
            occupied=set(),
            write=lambda key, data: facade.put_bytes(key, data),
        )


# --- leaf-without-stem (--leaf --source) -----------------------------------------------------------


def test_leaf_without_stem_reuses_shared_source_and_records_hierarchy_revision(store, facade):
    staged_v1 = _stage(store, _raw(V1), "v1")
    _, outcome1 = _derive_and_apply(facade, staged_v1, options={"extracted_at": "2026-01-01T00:00:00Z"})
    shared_source_key = next(k for k in outcome1.written if k.endswith(f"/{SOURCE_FILENAME}"))

    names1 = _names(_raw(V1))
    beam_guid = names1["a1-bm0"]

    staged_leaf = _stage(store, _raw(V2_LEAF), "leaf")
    publisher = IfcAssetPublisher()
    plan = publisher.derive(
        None,
        staged_leaf,
        storage=facade,
        collection="plant-a",
        options={"leaf": beam_guid, "source": shared_source_key, "extracted_at": "2026-01-03T00:00:00Z"},
    )
    occupied = set()
    outcome = apply_publish_plan(
        plan,
        published_by=PUBLISHED_BY,
        published_via="user",
        dry_run=False,
        replace_existing=False,
        occupied=occupied,
        write=lambda key, data: facade.put_bytes(key, data),
    )

    assert outcome.subjects == (beam_guid,)
    assert outcome.revision > outcome1.revision  # leaf revision > storey/site revision

    # the shared source blob was NOT re-uploaded: still exactly one `source.ifc` under the collection
    source_keys = [k for k in facade.list_keys(f"{ASSET_PREFIX}/plant-a/") if k.endswith(f"/{SOURCE_FILENAME}")]
    assert source_keys == [shared_source_key]

    provider = PublishedAssetProvider(store.reader(), provider_id=IFC_PROVIDER_ID)
    leaf_manifest = provider.manifest("plant-a", beam_guid)
    site_a_manifest = provider.manifest("plant-a", names1["SiteA"])
    assert leaf_manifest.hierarchy_revision == site_a_manifest.revision


# --- source_actor relay (Decision 6's IFC rule) -----------------------------------------------------


def _with_second_owner_history(raw: bytes, *, beam_name: str) -> bytes:
    """Give ONE product a second, distinct ``IfcOwnerHistory`` -- the file now has more than one,
    which is the trigger Decision 6 names (independent of which product this particular test asks
    about: "more than one owner history" is a file-wide fact, so EVERY product becomes eligible
    for relay once it is true, not just this one)."""
    f = ifcopenshell.file.from_string(raw.decode())
    person = f.create_entity("IfcPerson", Identification="bob", GivenName="Bob", FamilyName="Builder")
    org = f.create_entity("IfcOrganization", Name="Contractors Inc")
    pao = f.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
    app = f.create_entity(
        "IfcApplication",
        ApplicationDeveloper=org,
        Version="1.0",
        ApplicationFullName="BobCAD",
        ApplicationIdentifier="bobcad",
    )
    oh2 = f.create_entity(
        "IfcOwnerHistory", OwningUser=pao, OwningApplication=app, State="READWRITE", CreationDate=1790105999
    )
    beam = next(p for p in f.by_type("IfcBeam") if p.Name == beam_name)
    beam.OwnerHistory = oh2
    return f.to_string().encode()


def test_source_actor_absent_when_the_file_has_one_shared_owner_history(store, facade):
    """The plant-a corpus's ordinary case: adapy writes ONE owner history for the whole file and
    IfcProject has none (Decision 4's own instant-refusal test already documents this), so
    Decision 6's rule ("more than one owner history, OR differs from the project's") never fires
    and every manifest's `change` carries no `source_actor`."""
    staged = _stage(store, _raw(V1))
    publisher = IfcAssetPublisher()
    plan = publisher.derive(
        None, staged, storage=facade, collection="plant-a", options={"extracted_at": "2026-01-01T00:00:00Z"}
    )
    for write in plan.writes:
        if write.key.endswith("/asset.json"):
            manifest = parse_manifest(write.data)
            assert manifest.change is None or manifest.change.source_actor is None


def test_source_actor_relayed_when_the_file_has_more_than_one_owner_history(store, facade):
    modified = _with_second_owner_history(_raw(V1), beam_name="a1-bm0")
    staged = _stage(store, modified)
    publisher = IfcAssetPublisher()
    names = _names(modified)
    beam_guid = names["a1-bm0"]

    plan = publisher.derive(
        None,
        staged,
        storage=facade,
        collection="plant-a",
        options={"leaf": beam_guid, "extracted_at": "2026-01-01T00:00:00Z"},
    )
    (manifest_write,) = [w for w in plan.writes if w.key.endswith("/asset.json")]
    manifest = parse_manifest(manifest_write.data)
    assert manifest.change is not None
    assert manifest.change.source_actor == Actor(id="bob", display="Bob Builder", application="BobCAD 1.0")
    assert manifest.change.published_by is None  # still never set by the provider
