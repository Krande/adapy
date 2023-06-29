from __future__ import annotations

import enum
import importlib
import pkgutil
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem.formats.general import FEATypes


class FEA_IO(str, enum.Enum):
    read = "read"
    write = "write"


@dataclass
class FEATool:
    fea_type: FEATypes
    read: callable
    write: callable


# Define a global dictionary
TOOLS: dict[FEATypes, FEATool] = {}


def import_submodules():
    package = importlib.import_module("ada.fem.formats")

    for _, name, is_pkg in pkgutil.iter_modules(package.__path__):
        full_name = package.__name__ + "." + name
        importlib.import_module(full_name)


def get_tools() -> dict[FEATypes, FEATool]:
    import_submodules()
    return TOOLS


def tool_register(fem_format: FEATypes, io: FEA_IO):
    def inner_func(func):
        # Use the function name as the key
        # Ensure the function isn't already registered
        if fem_format not in TOOLS:
            TOOLS[fem_format] = FEATool(fem_format, None, None)

        if io == FEA_IO.read:
            TOOLS[fem_format].read = func
        elif io == FEA_IO.write:
            TOOLS[fem_format].write = func
        else:
            raise ValueError(f'Unrecognized io "{io}". Only "read" and "write" are supported')

        return func  # return original function, as we don't want to change its behavior

    return inner_func
