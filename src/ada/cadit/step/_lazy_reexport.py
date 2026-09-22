"""Lazy re-export for the back-compat shims under ``ada.cadit.step``.

Those shims exist so historical import paths keep working after the OCC implementations moved to
``ada.occ.step``. Their docstrings promised to do that "without itself importing OCC" -- but they
re-exported with a plain module-level ``from ada.occ.step... import ...``, which imports OCC the
moment the shim is imported. With no pythonocc installed, ``import ada.cadit.step.store`` then
failed before anything had asked for an OCC object at all.

That coupling was import-time only. At RUN time STEP already goes through the adacpp Part-21 path
by default (``from_step`` dispatches lazily and defaults to it); OCC is the fallback for files the
native reader does not cover yet. So the fix is to defer the import to first attribute access
(PEP 562 module ``__getattr__``), not to port anything:

* with OCC installed, ``from ada.cadit.step.store import StepStore`` behaves exactly as before;
* without it, importing the shim succeeds and only touching an OCC-backed name fails -- with the
  real ImportError naming the missing module, at the line that actually needed it.
"""

from __future__ import annotations

import importlib
import sys
from typing import Callable, Iterable


def lazy_reexport(
    module_name: str, target: str, names: Iterable[str]
) -> tuple[Callable[[str], object], Callable[[], list[str]]]:
    """Build a module-level ``(__getattr__, __dir__)`` pair re-exporting ``names`` from ``target``.

    Resolved values are cached into the shim's namespace, so the import runs once and later
    lookups are plain attribute reads.
    """
    exported = frozenset(names)

    def __getattr__(name: str) -> object:
        if name not in exported:
            raise AttributeError(f"module {module_name!r} has no attribute {name!r}")
        value = getattr(importlib.import_module(target), name)
        setattr(sys.modules[module_name], name, value)
        return value

    def __dir__() -> list[str]:
        return sorted(set(vars(sys.modules[module_name])) | exported)

    return __getattr__, __dir__
