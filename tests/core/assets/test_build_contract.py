"""``ada.assets.build``: fingerprint stability, key composition, GLB provenance round-trip, and
summary validation.

Golden fingerprints and keys are PINNED, not merely asserted non-empty: a fingerprint or a key
shape drifting silently is exactly the failure Decision 1's grammar-stability gate exists to catch
(``plan/v4/notes_core_asset_browser.md``, "Grammar stability").
"""

from __future__ import annotations

import json
import struct

import pytest

from ada.assets.build import (
    BuildError,
    BuildProvenance,
    BuildSummary,
    build_fingerprint,
    derived_asset_key,
    derived_asset_prefix,
    patch_glb_provenance,
    read_glb_provenance,
    validate_build_summary,
)

SOURCE_KEY = "assets/fixture-a/fixture-a/20260921T143001Z/source.jsonl"


def _fp(**overrides) -> str:
    kwargs = dict(
        options={"ref": "pump-b", "source_key": SOURCE_KEY},
        fingerprint_inputs=("source_key", "ref"),
        node="pump-b",
        hierarchy_source="20260921T143001Z",
    )
    kwargs.update(overrides)
    return build_fingerprint(**kwargs)


# --- build_fingerprint -----------------------------------------------------------------------


def test_fingerprint_is_pinned():
    """A golden value. If this changes, every derived key for every existing build changes with
    it -- the whole reason the grammar-stability gate pins it rather than just asserting shape."""
    assert _fp() == "eba9599c6db77cfb"


def test_fingerprint_ignores_an_option_not_named_by_fingerprint_inputs():
    """Adding an option nobody asked to be hashed must not re-key existing builds."""
    with_extra = build_fingerprint(
        options={"ref": "pump-b", "source_key": SOURCE_KEY, "unnamed_new_option": "anything"},
        fingerprint_inputs=("source_key", "ref"),
        node="pump-b",
        hierarchy_source="20260921T143001Z",
    )
    assert with_extra == _fp()


def test_fingerprint_treats_an_absent_input_and_its_default_value_as_the_same_request():
    """A request that never set an option and one that set it to the default are byte-for-byte
    the same request -- a default must never be emitted into the hash."""
    without = build_fingerprint(
        options={"ref": "pump-b", "source_key": SOURCE_KEY},
        fingerprint_inputs=("source_key", "ref", "quality"),  # 'quality' named but absent
        node="pump-b",
        hierarchy_source="20260921T143001Z",
    )
    at_default = build_fingerprint(
        options={"ref": "pump-b", "source_key": SOURCE_KEY, "quality": "default"},
        fingerprint_inputs=("source_key", "ref"),  # 'quality' not even named here
        node="pump-b",
        hierarchy_source="20260921T143001Z",
    )
    # Neither request hashes 'quality': the first because the value is absent, the second because
    # the input is not named. Same inputs hashed, same key.
    assert without == at_default == _fp()


def test_fingerprint_changes_with_node_and_hierarchy_source():
    """The two identity inputs that are not options must still move the key -- a different node,
    or the same node resolved through a different hierarchy, is a different build."""
    assert _fp(node="pump-a") != _fp()
    assert _fp(hierarchy_source="20260922T000000Z") != _fp()


# --- derived_asset_key / derived_asset_prefix -------------------------------------------------


def test_derived_asset_key_shape():
    key = derived_asset_key(
        provider="fixture-lines",
        collection="fixture-a",
        subject="pump-b",
        revision="20260921T143001Z",
        node="pump-b",
        fingerprint="eba9599c6db77cfb",
    )
    assert key == (
        "_derived/assets/fixture-lines/fixture-a/pump-b/20260921T143001Z/pump-b/eba9599c6db77cfb/summary.json"
    )


def test_derived_asset_prefix_uses_all_for_a_whole_subject_build():
    prefix = derived_asset_prefix(
        provider="fixture-lines",
        collection="fixture-a",
        subject="fixture-a",
        revision="20260921T143001Z",
        node=None,
        fingerprint="eba9599c6db77cfb",
    )
    assert prefix == "_derived/assets/fixture-lines/fixture-a/fixture-a/20260921T143001Z/all/eba9599c6db77cfb"


@pytest.mark.parametrize("bad", ["has/slash", "has..dots", ""])
def test_derived_asset_prefix_refuses_a_segment_that_is_not_one_path_segment(bad):
    with pytest.raises(BuildError):
        derived_asset_prefix(
            provider=bad, collection="fixture-a", subject="fixture-a", revision="r", node=None, fingerprint="f"
        )


# --- patch_glb_provenance / read_glb_provenance -------------------------------------------------


def _glb_with_bin(json_obj: dict, bin_data: bytes) -> bytes:
    """A GLB with both chunks, so the round-trip test can prove the binary chunk survives
    patching byte-for-byte and not merely 'the file still parses'."""
    body = json.dumps(json_obj, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)
    bin_padded = bin_data + b"\x00" * (-len(bin_data) % 4)
    total = 12 + 8 + len(body) + 8 + len(bin_padded)
    out = bytearray()
    out += b"glTF"
    out += struct.pack("<II", 2, total)
    out += struct.pack("<I4s", len(body), b"JSON")
    out += body
    out += struct.pack("<I4s", len(bin_padded), b"BIN\x00")
    out += bin_padded
    return bytes(out)


def _provenance() -> BuildProvenance:
    return BuildProvenance(
        provider="fixture-lines",
        collection="fixture-a",
        subject="pump-b",
        revision="20260921T143001Z",
        node="pump-b",
        fingerprint="eba9599c6db77cfb",
        built_at="2026-09-22T10:00:00Z",
        adapy_version="0.81.0",
        provider_version="fixture-lines-builder/1",
        hierarchy_source="20260921T143001Z",
        sources=({"key": SOURCE_KEY, "sha256": "ab" * 32},),
    )


def test_patch_and_read_round_trip():
    provenance = _provenance()
    original = _glb_with_bin({"asset": {"version": "2.0"}}, b"\x00\x01\x02\x03" * 32)
    patched = patch_glb_provenance(original, provenance, generator="fixture-lines 1 (ada-py 0.81.0)")

    read_back = read_glb_provenance(patched)
    assert read_back == provenance.to_dict()

    doc = json.loads(patched[20 : 20 + struct.unpack_from("<I", patched, 12)[0]].decode("utf-8"))
    assert doc["asset"]["generator"] == "fixture-lines 1 (ada-py 0.81.0)"


def test_patch_glb_provenance_leaves_the_binary_chunk_byte_identical():
    bin_data = bytes(range(256)) * 4  # large enough that an off-by-one would show
    bin_padded = bin_data + b"\x00" * (-len(bin_data) % 4)
    bin_chunk_bytes = struct.pack("<I4s", len(bin_padded), b"BIN\x00") + bin_padded

    original = _glb_with_bin({"asset": {"version": "2.0"}}, bin_data)
    assert original.endswith(bin_chunk_bytes)

    patched = patch_glb_provenance(original, _provenance())
    assert patched.endswith(bin_chunk_bytes), "the binary chunk must be copied through untouched"


def test_read_glb_provenance_is_none_without_a_provenance_block():
    plain = _glb_with_bin({"asset": {"version": "2.0"}}, b"")
    assert read_glb_provenance(plain) is None


def test_patch_glb_provenance_refuses_a_non_glb():
    with pytest.raises(BuildError):
        patch_glb_provenance(b"not a glb", _provenance())


# --- validate_build_summary --------------------------------------------------------------------


def _valid_summary() -> BuildSummary:
    provenance = _provenance()
    prefix = derived_asset_prefix(
        provider=provenance.provider,
        collection=provenance.collection,
        subject=provenance.subject,
        revision=provenance.revision,
        node=provenance.node,
        fingerprint=provenance.fingerprint,
    )
    return BuildSummary(ok=True, glb_key=f"{prefix}/model.glb", provenance=provenance)


def _validate(summary: BuildSummary) -> None:
    p = summary.provenance
    prefix = derived_asset_prefix(
        provider=p.provider,
        collection=p.collection,
        subject=p.subject,
        revision=p.revision,
        node=p.node,
        fingerprint=p.fingerprint,
    )
    validate_build_summary(
        summary,
        provider="fixture-lines",
        collection="fixture-a",
        subject="pump-b",
        revision="20260921T143001Z",
        node="pump-b",
        fingerprint="eba9599c6db77cfb",
        derived_prefix=prefix,
    )


def test_validate_build_summary_accepts_a_summary_that_answers_its_own_request():
    _validate(_valid_summary())  # must not raise


@pytest.mark.parametrize(
    "field, bad_value",
    [
        ("provider", "someone-else"),
        ("collection", "wrong-collection"),
        ("subject", "wrong-subject"),
        ("revision", "20200101T000000Z"),
        ("node", "wrong-node"),
        ("fingerprint", "0000000000000000"),
    ],
)
def test_validate_build_summary_refuses_each_disagreeing_field_by_name(field, bad_value):
    provenance = _provenance()
    provenance = _replace_provenance_field(provenance, field, bad_value)
    prefix = derived_asset_prefix(
        provider="fixture-lines",
        collection="fixture-a",
        subject="pump-b",
        revision="20260921T143001Z",
        node="pump-b",
        fingerprint="eba9599c6db77cfb",
    )
    summary = BuildSummary(ok=True, glb_key=f"{prefix}/model.glb", provenance=provenance)
    with pytest.raises(BuildError) as exc:
        validate_build_summary(
            summary,
            provider="fixture-lines",
            collection="fixture-a",
            subject="pump-b",
            revision="20260921T143001Z",
            node="pump-b",
            fingerprint="eba9599c6db77cfb",
            derived_prefix=prefix,
        )
    assert f"provenance.{field}" in str(exc.value)


def _replace_provenance_field(provenance: BuildProvenance, field: str, value) -> BuildProvenance:
    kwargs = dict(
        provider=provenance.provider,
        collection=provenance.collection,
        subject=provenance.subject,
        revision=provenance.revision,
        node=provenance.node,
        fingerprint=provenance.fingerprint,
        built_at=provenance.built_at,
    )
    kwargs[field] = value
    return BuildProvenance(**kwargs)


def test_validate_build_summary_refuses_a_glb_key_outside_the_composed_prefix():
    summary = _valid_summary()
    outside = BuildSummary(
        ok=True, glb_key="_derived/assets/some/other/prefix/model.glb", provenance=summary.provenance
    )
    with pytest.raises(BuildError, match="outside the prefix"):
        _validate(outside)


def test_validate_build_summary_refuses_ok_false():
    provenance = _provenance()
    summary = BuildSummary(ok=False, glb_key="", provenance=provenance, error="builder exploded")
    with pytest.raises(BuildError, match="builder exploded"):
        _validate(summary)
