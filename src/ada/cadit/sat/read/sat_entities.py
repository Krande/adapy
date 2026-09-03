from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.cadit.sat.store import SatStore


@dataclass
class AcisSubType:
    type: str
    chunks: list[str]
    parent_record: AcisRecord
    _string: str

    def get_as_string(self) -> str:
        return self._string

    @staticmethod
    def from_string(s: str, parent_record: AcisRecord) -> AcisSubType:
        chunks = s.split()
        return AcisSubType(chunks[0], chunks, parent_record, s)


# ACIS writes an entity-header block between a record's attribute pointer and
# the record's own data. Older save formats omit that block entirely (observed
# on files written at save version 500), and its width is not the same for every
# record family: general entities carry ``<int> <int> $ptr``, while ATTRIB -
# and, in every layout observed, TRANSFORM - carry a single ``<int>``.
#
# Every chunk offset in this package is written against the block-present
# layout, so a record missing the block has each of its fields three (or one)
# slots to the left. Reading a face's loop pointer at the fixed offset then
# lands on the surface pointer instead, and the "next loop" chase walks off the
# end of an unrelated record and tries to resolve the ``#`` end-of-record marker
# as a record id. Pad the absent block at parse time so a single layout reaches
# the rest of the reader.
#
# The pad is ``$-1`` - the null pointer - so a field the old format never wrote
# resolves to "nothing" rather than to some other record if it is ever read.
_ABSENT_FIELD = "$-1"

#: Width of the entity-header block, by record family.
_NARROW_BLOCK = 1
_WIDE_BLOCK = 3


def _is_int_token(token: str) -> bool:
    try:
        int(token)
    except ValueError:
        return False
    return True


def _entity_block_width(record_type: str) -> int:
    return _NARROW_BLOCK if record_type.endswith("attrib") or record_type == "transform" else _WIDE_BLOCK


def _has_entity_block(chunks: list[str], width: int) -> bool:
    """Does this record already carry its entity-header block?

    The block's leading fields are integers and - for the wide form - its last
    field is a pointer. A record that omits the block has its own data there
    instead, which begins with a pointer for every topology record and with a
    coordinate for every geometry one; neither can be mistaken for the block.
    """
    if width == _NARROW_BLOCK:
        return len(chunks) > 3 and _is_int_token(chunks[3])
    return len(chunks) > 5 and _is_int_token(chunks[3]) and _is_int_token(chunks[4]) and chunks[5].startswith("$")


def normalize_entity_block(chunks: list[str]) -> list[str]:
    """Return ``chunks`` in the block-present layout, padding the block if absent."""
    if len(chunks) < 3:
        return chunks
    width = _entity_block_width(chunks[1])
    if _has_entity_block(chunks, width):
        return chunks
    return chunks[:3] + [_ABSENT_FIELD] * width + chunks[3:]


@dataclass
class AcisRecord:
    type: str
    chunks: list[str]
    index: int
    _string: str | None
    sat_store: SatStore | None = None

    @staticmethod
    def from_string(s: str) -> AcisRecord:
        chunks = normalize_entity_block(s.split())
        index = int(chunks[0][1:])
        return AcisRecord(chunks[1], chunks, index, s)

    def get_as_string(self) -> str:
        return self._string

    def get_name(self) -> str:
        return self.sat_store.get_name(self.chunks[2])

    def get_sub_type_str(self):
        spline_data_str = self.get_as_string()
        split_data = spline_data_str.split("{", 1)
        return split_data[1].rsplit("}")[0].strip() + " }"

    def get_sub_type(self) -> AcisSubType:
        return AcisSubType.from_string(self.get_sub_type_str(), self)

    def __repr__(self):
        return f"AcisRecord(index={self.index}, type={self.type})"
