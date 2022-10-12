from __future__ import annotations

from enum import Enum


class GeomRepr(Enum):
    SOLID = "solid"
    SHELL = "shell"
    LINE = "line"

    @staticmethod
    def from_str(value: str) -> GeomRepr:
        keymap = {x.value.lower(): x for x in GeomRepr}
        result = keymap.get(value.lower())
        if result is None:
            raise ValueError(f"Geometric Representation needs to be one of {list(keymap.keys())}")

        return result
