"""Extensible pydantic <-> Excel (.xlsx) serialization.

A generic engine that maps pydantic models to worksheets using the class hints
the models already declare (``SHEET_NAME``/``TAB_COLOR``/``ORIENTATION``/
``HIDE_IN_EXCEL``) plus per-field ``excel`` metadata. Types are (de)serialized by
a registry of :class:`ColumnCodec`; an unclaimed type raises rather than silently
losing data. Downstream packages extend it by registering their own models,
codecs and layouts — no fork of the engine.

Typical use::

    from ada.serialize.xlsx import WorkbookSerializer
    s = WorkbookSerializer().register(TopoSpace).register(TopoEquipment)
    s.write(list_of_instances, "model.xlsx")
    by_type = s.read("model.xlsx")   # {TopoSpace: [...], TopoEquipment: [...]}
"""

from __future__ import annotations

from .codecs import (
    BoolCodec,
    CodecRegistry,
    ColumnCodec,
    EnumCodec,
    JsonListCodec,
    NoCodecError,
    ScalarCodec,
    core_type,
    default_registry,
    excel_meta,
)
from .serializer import SCHEMA_VERSION, WorkbookSerializer
from .spec import SheetSpec, columns_for

__all__ = [
    "WorkbookSerializer",
    "SheetSpec",
    "columns_for",
    "CodecRegistry",
    "ColumnCodec",
    "NoCodecError",
    "ScalarCodec",
    "BoolCodec",
    "EnumCodec",
    "JsonListCodec",
    "default_registry",
    "core_type",
    "excel_meta",
    "SCHEMA_VERSION",
]
