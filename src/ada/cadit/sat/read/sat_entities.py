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


@dataclass
class AcisRecord:
    type: str
    chunks: list[str]
    index: int
    _string: str | None
    sat_store: SatStore | None = None

    @staticmethod
    def from_string(s: str) -> AcisRecord:
        chunks = s.split()
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
