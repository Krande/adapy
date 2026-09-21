"""The Python half of the shared grammar vectors.

The TypeScript mirror reads the SAME file. Two implementations of one grammar drift unless
something forces them together; this is that something.
"""

import json
from pathlib import Path

import pytest

from ada.assets.keys import (
    AssetKeyError,
    asset_key,
    parse_asset_key,
    revision_from_instant,
)

VECTORS = json.loads((Path(__file__).parent / "vectors" / "asset_key_vectors.json").read_text())


@pytest.mark.parametrize("case", VECTORS["valid"], ids=lambda c: c["why"])
def test_valid_vectors_parse_and_recompose(case):
    parsed = parse_asset_key(case["key"])
    assert parsed.collection == case["collection"]
    assert parsed.subject == case["subject"]
    assert parsed.revision == case["revision"]
    assert parsed.filename == case["filename"]
    assert parsed.is_collection_level is case["collection_level"]
    assert asset_key(parsed.collection, parsed.subject, parsed.revision, parsed.filename) == case["key"]


@pytest.mark.parametrize("case", VECTORS["invalid"], ids=lambda c: c["why"])
def test_invalid_vectors_are_refused(case):
    with pytest.raises(AssetKeyError):
        parse_asset_key(case["key"])


@pytest.mark.parametrize("case", VECTORS["instants"], ids=lambda c: c["why"])
def test_instant_vectors_normalise(case):
    assert revision_from_instant(case["in"]) == case["out"]


@pytest.mark.parametrize("naive", VECTORS["naive_instants"])
def test_naive_instants_are_refused(naive):
    with pytest.raises(AssetKeyError):
        revision_from_instant(naive)


def test_ordering_vector():
    chronological = VECTORS["ordering"]["chronological"]
    assert sorted(chronological) == chronological
