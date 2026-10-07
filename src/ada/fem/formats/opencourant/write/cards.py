"""Fixed-width card helpers for OpenCourant / OpenRadioss input decks.

Radioss reads 10-character integer and 20-character real fields; every card the
writer emits is built from these two field widths.
"""

from __future__ import annotations

from typing import Iterable

SEP = "#---1----|----2----|----3----|----4----|----5----|----6----|----7----|----8----|----9----|---10----|"


def i10(value: int) -> str:
    return f"{int(value):>10d}"


def f20(value: float) -> str:
    text = f"{float(value):.12g}"
    if len(text) > 20:
        text = f"{float(value):.10e}"
    return f"{text:>20s}"


def title(text: str) -> str:
    return str(text)[:100]


def int_rows(values: Iterable[int], per_row: int = 10) -> list[str]:
    values = list(values)
    return ["".join(i10(v) for v in values[i : i + per_row]) for i in range(0, len(values), per_row)]


def comment(text: str) -> str:
    return f"#{text}"
