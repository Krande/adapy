"""The concepts verb: a provider's own format read into ``ada`` objects, for a clash check.

The claim under test is the seam itself. Core's clash is model-centred -- `identify_joints` walks
`get_all_physical_objects(Beam/Plate)` and the detail hand-off wants those same objects -- and every
existing way in reaches a model through a file whose extension core recognises. A provider whose
format has no such extension answers through this instead, and core still never learns the format:
the fixture provider below reads a private text source, and nothing here imports a reader for it.
"""

from __future__ import annotations

import pytest

from ada.assets.concepts import (
    AssetConceptsError,
    asset_concepts,
    clear_asset_concepts,
    concept_providers,
    part_for_manifest,
    register_asset_concepts,
)
from ada.assets.manifest import ArtefactEntry, AssetManifest, BuildSpec
from ada.assets.provider import AssetConcepts, provider_capabilities

PROVIDER = "fixture-lines"


class FakeStorage:
    """The sync facade a builder is handed: read, list. A concepts reader uses the read half."""

    def __init__(self, blobs: dict[str, bytes]):
        self.blobs = dict(blobs)
        self.reads: list[str] = []

    def get_bytes(self, key: str) -> bytes:
        self.reads.append(key)
        try:
            return self.blobs[key]
        except KeyError:
            raise FileNotFoundError(key) from None

    def list_keys(self, prefix: str):
        return [k for k in sorted(self.blobs) if k.startswith(prefix)]


class LinesConcepts:
    """A reader for a format core has never heard of: one member per line."""

    def concepts(self, options, *, storage, scope=None, node=None):
        import ada

        raw = storage.get_bytes(options["source_key"]).decode()
        part = ada.Part(node or "lines")
        for line in (ln for ln in raw.splitlines() if ln.strip()):
            name, x0, y0, z0, x1, y1, z1 = line.split()
            part.add_beam(
                ada.Beam(name, (float(x0), float(y0), float(z0)), (float(x1), float(y1), float(z1)), "IPE200")
            )
        return part


SOURCE_KEY = "assets/fixture-a/unit-1/20260926T000000Z/lines.txt"
SOURCE = b"\n".join(
    [
        b"bm0 0 0 0 4 0 0",
        b"bm1 4 0 0 4 4 0",  # meets bm0 end-to-end at (4,0,0)
        b"bm2 20 20 0 24 20 0",  # nowhere near either
    ]
)


def _manifest(delivery="build", with_source=True):
    return AssetManifest(
        provider=PROVIDER,
        collection="fixture-a",
        subject="unit-1",
        revision="20260926T000000Z",
        node="unit-1",
        produced_at="2026-09-26T00:00:00Z",
        published_at="2026-09-26T00:00:00Z",
        delivery=delivery,
        build=BuildSpec(capability="build-lines", options={"source_key": SOURCE_KEY}) if delivery == "build" else None,
        artefacts=((ArtefactEntry(role="source", key=SOURCE_KEY, sha256="", size=len(SOURCE)),) if with_source else ()),
    )


@pytest.fixture
def registered():
    register_asset_concepts(PROVIDER, lambda: LinesConcepts(), label="Fixture lines")
    yield FakeStorage({SOURCE_KEY: SOURCE})
    clear_asset_concepts()


# --- the protocol -------------------------------------------------------------------------------


def test_the_capability_is_read_off_the_object_like_every_other():
    assert provider_capabilities(LinesConcepts())["concepts"] is True
    assert isinstance(LinesConcepts(), AssetConcepts)

    class TreeOnly:
        def collections(self, scope): ...
        def hierarchy(self, scope, collection, *, root=None, depth=1): ...
        def delivery(self, scope, collection, node, *, revision=None): ...

    assert provider_capabilities(TreeOnly())["concepts"] is False


# --- the registry -------------------------------------------------------------------------------


def test_a_process_without_the_reader_says_so_by_name():
    """Not an empty model: a clash check over nothing would report "no joints", which is a
    confident wrong answer."""
    clear_asset_concepts()
    with pytest.raises(AssetConceptsError, match="cannot be read into objects"):
        asset_concepts("nobody-registered-this")


def _register_from_one_place():
    """What a plugin's own `register()` does, reproduced faithfully: a lambda built at ONE source
    location, so calling this twice makes two function objects with the same origin."""
    register_asset_concepts(PROVIDER, lambda: LinesConcepts())


def test_an_entry_point_loaded_twice_is_tolerated_and_a_rival_is_not():
    """Idempotent by ORIGIN, not by identity. A `register()` that closes over anything returns a
    new function object per call, so identity would refuse the exact case this tolerates -- an
    entry point reached by discovery AND by an explicit preload."""
    clear_asset_concepts()
    try:
        _register_from_one_place()
        _register_from_one_place()  # the same code, run again: not a conflict

        def other_factory():  # pragma: no cover - never called
            return LinesConcepts()

        with pytest.raises(AssetConceptsError, match="may not take it over"):
            register_asset_concepts(PROVIDER, other_factory)
    finally:
        clear_asset_concepts()


def test_a_reader_that_reports_itself_unavailable_is_refused_not_used():
    """A registered reader whose dependency is missing must fail by name rather than half-read."""
    register_asset_concepts("gated", lambda: LinesConcepts(), available=lambda: False)
    try:
        with pytest.raises(AssetConceptsError, match="unavailable"):
            asset_concepts("gated")
        assert concept_providers() == [{"id": "gated", "label": "gated", "available": False}]
    finally:
        clear_asset_concepts()


def test_a_broken_availability_probe_is_reported_rather_than_raised():
    def boom():
        raise RuntimeError("probe exploded")

    register_asset_concepts("broken", lambda: LinesConcepts(), available=boom)
    try:
        row = concept_providers()[0]
        assert row["available"] is False
        assert "probe exploded" in row["error"]
    finally:
        clear_asset_concepts()


# --- manifest -> Part ---------------------------------------------------------------------------


def test_the_node_comes_back_as_real_ada_objects(registered):
    import ada

    part = part_for_manifest(_manifest(), storage=registered)
    beams = list(part.get_all_physical_objects(by_type=ada.Beam))
    assert {b.name for b in beams} == {"bm0", "bm1", "bm2"}
    assert registered.reads == [SOURCE_KEY]


def test_the_options_passed_through_are_the_nodes_own_build_options(registered):
    """Nothing extra is recorded at publish time: a node that can be built can be read."""
    seen = {}

    class Recording(LinesConcepts):
        def concepts(self, options, *, storage, scope=None, node=None):
            seen.update({"options": dict(options), "node": node})
            return super().concepts(options, storage=storage, scope=scope, node=node)

    clear_asset_concepts()
    register_asset_concepts(PROVIDER, lambda: Recording())
    part_for_manifest(_manifest(), storage=registered)
    assert seen["options"] == {"source_key": SOURCE_KEY}
    assert seen["node"] == "unit-1"


def test_a_subject_with_no_build_options_is_refused_rather_than_guessed(registered):
    """A provider asked for objects with no source named would have to invent the scope, and a
    check over an invented scope is worse than one that did not run."""
    with pytest.raises(AssetConceptsError, match="nothing here naming the source"):
        part_for_manifest(_manifest(delivery="none"), storage=registered)


# --- and the clash over it ----------------------------------------------------------------------


def test_a_clash_check_runs_over_a_published_node_of_an_unreadable_format(registered):
    from ada.clash.from_asset import clash_check_from_asset_node

    manifest = _manifest()
    registered.blobs["assets/fixture-a/unit-1/20260926T000000Z/asset.json"] = manifest.to_json()

    doc = clash_check_from_asset_node(collection="fixture-a", subject="unit-1", storage=registered)
    assert doc["schema"].startswith("ada.clash/result@")
    # bm0 and bm1 meet end-to-end; bm2 is 20 m away and joins nothing.
    assert doc["counts"]["beams"] == 3
    assert doc["joints"], "the two touching beams produced no joint"
    joined = {m["name"] for joint in doc["joints"] for m in joint["members"]}
    assert joined == {"bm0", "bm1"}
    assert "bm2" not in joined  # 20 m away, and a check that "found" it would be finding nothing


def test_the_result_says_a_provider_read_it(registered):
    """A joint list is only as trustworthy as the read behind it, so the document records which
    provider produced the model rather than leaving it to be inferred from the route."""
    from ada.clash.from_asset import clash_check_from_asset_node

    manifest = _manifest()
    registered.blobs["assets/fixture-a/unit-1/20260926T000000Z/asset.json"] = manifest.to_json()
    doc = clash_check_from_asset_node(collection="fixture-a", subject="unit-1", storage=registered)
    prov = doc.get("provenance") or {}
    assert prov.get("reader") == "provider-concepts"
    assert prov.get("provider") == PROVIDER
    assert doc["source_key"] == SOURCE_KEY


def test_an_unpublished_subject_is_a_missing_file_not_an_empty_check(registered):
    from ada.clash.from_asset import clash_check_from_asset_node

    with pytest.raises(FileNotFoundError, match="no published manifest"):
        clash_check_from_asset_node(collection="fixture-a", subject="nope", storage=registered)
