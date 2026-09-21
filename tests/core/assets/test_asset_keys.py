"""The five-segment asset key grammar.

What is pinned here is the part of the contract that is expensive to change later: the derived
key and every stored blob are composed from this, so a segment that parses today must parse
forever, and one that is refused must stay refused.
"""

import datetime as dt

import pytest

from ada.assets.keys import (
    ASSET_KEY_SEGMENTS,
    AssetKey,
    AssetKeyError,
    asset_key,
    parse_asset_key,
    revision_from_instant,
    staging_prefix,
)

REV = "20260921T143001Z"


def test_roundtrip():
    key = asset_key("plant-a", "1cPZ5wLayHxeFFv5utukF4", REV, "asset.json")
    assert key == f"assets/plant-a/1cPZ5wLayHxeFFv5utukF4/{REV}/asset.json"
    assert parse_asset_key(key) == AssetKey("plant-a", "1cPZ5wLayHxeFFv5utukF4", REV, "asset.json")
    assert str(parse_asset_key(key)) == key


def test_ifc_globalid_is_a_subject_verbatim():
    """The §Decision 7 contract: a 22-char GlobalId is a subject as-is, not hex-expanded.

    Its alphabet is 0-9A-Za-z_$ and the first character is always 0-3, so the only characters
    that could have forced an encoding are '_' (legal, just not leading) and '$'.
    """
    guid = "1cPbYcLayHxe$Cv5utukF4"
    assert len(guid) == 22
    assert parse_asset_key(asset_key("c", guid, REV, "asset.json")).subject == guid


def test_collection_level_is_equality_not_shape():
    """A collection-level subject is one EQUAL to the collection -- never a regex on the subject."""
    assert parse_asset_key(asset_key("plant-a", "plant-a", REV, "hierarchy.json")).is_collection_level
    assert not parse_asset_key(asset_key("plant-a", "plant-b", REV, "hierarchy.json")).is_collection_level


# --- the three refusals named in the Phase 1 acceptance criteria -------------------------------


@pytest.mark.parametrize("bad", ["plant/a", "a/b", "sub/dir"])
def test_refuses_slash_inside_a_segment(bad):
    with pytest.raises(AssetKeyError, match="invalid (collection|subject)"):
        asset_key(bad, "n1", REV, "asset.json")


def test_refuses_wrong_arity_and_says_so():
    """Arity is the checksum, and it is checked before any segment is inspected."""
    four = f"assets/plant-a/n1/{REV}"
    with pytest.raises(AssetKeyError, match=f"expected exactly {ASSET_KEY_SEGMENTS} segments"):
        parse_asset_key(four)
    with pytest.raises(AssetKeyError, match=f"expected exactly {ASSET_KEY_SEGMENTS} segments"):
        parse_asset_key(f"assets/plant-a/n1/{REV}/sub/asset.json")


@pytest.mark.parametrize(
    "bad_revision",
    [
        "2026-09-21T14:30:01Z",  # not compact
        "20260921T143001",  # no Z -- would not sort against the Z-suffixed ones
        "20260921T143001+0200",  # an offset is not UTC
        "20260921",  # date only
    ],
)
def test_refuses_non_utc_revision_and_names_the_fix(bad_revision):
    with pytest.raises(AssetKeyError, match="revision_from_instant"):
        asset_key("plant-a", "n1", bad_revision, "asset.json")


# --- reserved '_' and staging -------------------------------------------------------------------


def test_leading_underscore_is_reserved_for_core():
    with pytest.raises(AssetKeyError, match="reserved for core"):
        asset_key("_staging", "n1", REV, "asset.json")


def test_staging_key_never_parses_as_an_asset():
    """Four segments by construction, whatever the staging id looks like."""
    staged = staging_prefix("upload-123") + "source.jsonl"
    assert staged.count("/") == 3
    with pytest.raises(AssetKeyError, match="4 segments"):
        parse_asset_key(staged)


@pytest.mark.parametrize("bad_first", ["_leading", ".dot", "-dash"])
def test_first_character_is_narrower_than_the_rest(bad_first):
    """No '.', '-' or '_' first: keeps '.'/'..' out and keys from reading as CLI flags."""
    with pytest.raises(AssetKeyError):
        asset_key(bad_first, "n1", REV, "asset.json")
    assert parse_asset_key(asset_key("a" + bad_first, "n1", REV, "asset.json")).collection == "a" + bad_first


# --- revision normalisation ---------------------------------------------------------------------


def test_revision_from_instant_normalises_to_utc():
    assert revision_from_instant("2026-09-21T14:30:01Z") == REV
    assert revision_from_instant("2026-09-21T16:30:01+02:00") == REV  # same instant, other offset
    aware = dt.datetime(2026, 9, 21, 14, 30, 1, tzinfo=dt.timezone.utc)
    assert revision_from_instant(aware) == REV


def test_naive_instant_is_refused_not_assumed_utc():
    """Guessing the zone would place the revision at the wrong point in an ordering that is
    unrecoverable once neighbouring revisions exist."""
    with pytest.raises(AssetKeyError, match="no timezone"):
        revision_from_instant(dt.datetime(2026, 9, 21, 14, 30, 1))
    with pytest.raises(AssetKeyError, match="no timezone"):
        revision_from_instant("2026-09-21T14:30:01")


def test_lexical_order_is_chronological_order():
    """The property the whole index fold leans on: newest == max(revisions), no parsing."""
    instants = ["2026-01-02T03:04:05Z", "2026-09-21T14:30:01Z", "2025-12-31T23:59:59Z"]
    revisions = [revision_from_instant(i) for i in instants]
    assert sorted(revisions) == [revision_from_instant(i) for i in sorted(instants)]
