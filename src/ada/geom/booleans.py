from dataclasses import dataclass
from enum import Enum
from typing import Any


class BooleanOperatorEnum(Enum):
    UNION = "UNION"
    INTERSECTION = "INTERSECTION"
    DIFFERENCE = "DIFFERENCE"


@dataclass
class BooleanResult:
    first_operand: Any
    second_operand: Any
    operator: BooleanOperatorEnum



