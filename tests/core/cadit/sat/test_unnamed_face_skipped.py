"""Regression: a SAT face with no name attribute must be skipped, not fatal.

``iter_flat_plates`` walks every ``face`` record and resolves its name through
the attribute pointer. A face carrying no attribute, or one whose attribute
chain ends before a string attrib, resolves to an empty name — which used to
fall into the prefix guard and abort the whole model import. Plates are matched
to their XML element by name, so such a face can never become a plate and
skipping it is lossless; a face that *is* named but uses an unexpected scheme
still raises.
"""

from __future__ import annotations

import pytest

from ada.cadit.sat.read.faces import PlateFactory
from ada.cadit.sat.store import SatStore

# Minimal synthetic ACIS records. Chunk 2 of a ``face`` is its attribute
# pointer; chunk 4 of an attribute is the next-attribute pointer.
FACE_NO_ATTRIBUTE = "-3 face $-1 -1 -1 $-1 $-1 $5 $2 $-1 $6 forward double out F F #"
FACE_COLOUR_ONLY = "-13 face $14 -1 -1 $-1 $-1 $5 $2 $-1 $6 forward double out F F #"
COLOUR_ATTRIBUTE = "-14 rgb_color-st-attrib $-1 -1 $-1 $-1 $13 1 0 0 #"
FACE_FOREIGN_NAME = "-23 face $24 -1 -1 $-1 $-1 $5 $2 $-1 $6 forward double out F F #"
FOREIGN_NAME_ATTRIBUTE = "-24 string_attrib-name_attrib-gen-attrib $-1 -1 $-1 $-1 $23 @15 SOME_OTHER_ID @2 35 #"


def _factory(*records: str) -> PlateFactory:
    store = SatStore()
    for record in records:
        store.add(record)
    return PlateFactory(store)


@pytest.mark.parametrize(
    "records, face_index",
    [
        pytest.param((FACE_NO_ATTRIBUTE,), 3, id="no-attribute"),
        pytest.param((FACE_COLOUR_ONLY, COLOUR_ATTRIBUTE), 13, id="colour-attribute-only"),
    ],
)
def test_face_without_a_name_is_skipped(records, face_index):
    factory = _factory(*records)
    face = factory.sat_store.get(face_index)
    assert factory.sat_store.get_name(face.chunks[PlateFactory.name_idx]) == ""
    # Skipped like any other unusable face — no exception, no plate.
    assert factory.get_face_name_and_points(face) is None


def test_face_with_an_unexpected_name_still_raises():
    factory = _factory(FACE_FOREIGN_NAME, FOREIGN_NAME_ATTRIBUTE)
    face = factory.sat_store.get(23)
    with pytest.raises(NotImplementedError, match="35"):
        factory.get_face_name_and_points(face)
