"""Folding a flat listing into collection -> subject -> revision -> files."""

from ada.assets.index import fold_listing

R1, R2, R3 = "20260101T000000Z", "20260501T120000Z", "20260921T143001Z"

KEYS = [
    f"assets/plant-a/plant-a/{R2}/hierarchy.json",
    f"assets/plant-a/plant-a/{R2}/asset.json",
    f"assets/plant-a/n1/{R1}/asset.json",
    f"assets/plant-a/n1/{R3}/asset.json",
    f"assets/plant-a/n1/{R3}/source.jsonl",
    f"assets/plant-b/n9/{R1}/asset.json",
    "assets/_staging/upload-77/source.jsonl",  # staged: not an asset yet
    "_derived/assets/whatever/summary.json",  # not ours
]


def test_folds_into_collections_subjects_revisions():
    idx = fold_listing(KEYS)
    assert sorted(idx.collections) == ["plant-a", "plant-b"]
    n1 = idx.subject("plant-a", "n1")
    assert [r.revision for r in n1.revisions] == [R3, R1]  # newest first
    assert n1.revisions[0].files == ("asset.json", "source.jsonl")


def test_newest_first_is_a_plain_reverse_sort():
    """No date parsing: the compact-UTC revision form makes lexical order chronological."""
    idx = fold_listing([f"assets/c/s/{r}/asset.json" for r in (R2, R1, R3)])
    assert [r.revision for r in idx.subject("c", "s").revisions] == [R3, R2, R1]
    assert idx.subject("c", "s").latest.revision == R3


def test_staged_uploads_are_skipped_not_reported_malformed():
    """They share the prefix by design; 'malformed' is for publisher bugs, not for these."""
    idx = fold_listing(KEYS)
    assert idx.malformed == ()
    assert "_staging" not in idx.collections


def test_malformed_keys_are_surfaced_with_a_reason():
    """A publisher that cannot see its own bad keys keeps writing them."""
    bad = "assets/plant-a/n1/2026-09-21T14:30:01Z/asset.json"  # non-compact revision
    idx = fold_listing([bad])
    assert len(idx.malformed) == 1
    assert idx.malformed[0].key == bad
    assert "revision_from_instant" in idx.malformed[0].reason


def test_half_written_publish_is_visible_as_missing_manifest():
    """Manifests are written last, so no asset.json means the publish died partway."""
    idx = fold_listing([f"assets/c/s/{R1}/source.jsonl", f"assets/c/s/{R2}/asset.json"])
    revs = {r.revision: r for r in idx.subject("c", "s").revisions}
    assert revs[R1].has_manifest is False
    assert revs[R2].has_manifest is True
    assert idx.subject("c", "s").latest.revision == R2
    assert idx.subject("c", "s").latest_complete.revision == R2


def test_latest_complete_skips_a_newer_half_written_revision():
    idx = fold_listing([f"assets/c/s/{R1}/asset.json", f"assets/c/s/{R3}/source.jsonl"])
    s = idx.subject("c", "s")
    assert s.latest.revision == R3  # newest by time
    assert s.latest_complete.revision == R1  # newest that is actually readable


def test_collection_level_subject_folds_beside_node_subjects():
    idx = fold_listing(KEYS)
    subs = {s.subject for s in idx.subjects("plant-a")}
    assert subs == {"plant-a", "n1"}
