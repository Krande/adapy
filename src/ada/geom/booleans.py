from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any


class BoolOpEnum(Enum):
    UNION = "UNION"
    INTERSECTION = "INTERSECTION"
    DIFFERENCE = "DIFFERENCE"

    @classmethod
    def from_str(cls, value) -> BoolOpEnum:
        enum_map = {x.value.lower(): x for x in cls}
        return enum_map.get(value.lower())


@dataclass
class BooleanResult:
    first_operand: Any
    second_operand: Any
    operator: BoolOpEnum
