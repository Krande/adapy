from __future__ import annotations

from enum import Enum


class BaseEnum(Enum):
    @classmethod
    def from_str(cls, value: str):
        if isinstance(value, cls):
            return value
        key_map = {x.value.lower(): x for x in cls}
        return key_map.get(value.lower(), None)


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
