from __future__ import annotations

from dataclasses import dataclass, field

from ada.fem.formats.general import FEATypes


@dataclass
class FeaResultSet:
    name: str
    step: int
    components: list[str]
    values: list[tuple] = field(repr=False)


@dataclass
class FeaResult:
    name: str
    software: str | FEATypes
    results: list[FeaResultSet]
