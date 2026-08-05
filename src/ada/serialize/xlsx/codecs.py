"""Column codecs: the type <-> Excel-cell contract.

A :class:`ColumnCodec` owns both directions of one field's serialization —
``to_cell`` (python value -> cell) and ``from_cell`` (cell -> python value, before
pydantic validation). The :class:`CodecRegistry` picks a codec per field: an
explicit ``codec`` name in the field's ``excel`` metadata wins; otherwise the
first codec whose ``matches`` accepts the field's (Optional-stripped) core type.

A field type that no codec claims raises :class:`NoCodecError` rather than
silently ``repr()``-ing the value — so a new/nested type can never round-trip
lossily unnoticed. A downstream package registers its own codec (by name, via
field metadata) to teach the engine that type.
"""

from __future__ import annotations

import enum
import json
import types
import typing
from typing import (
    Any,
    Literal,
    Protocol,
    Union,
    get_args,
    get_origin,
    runtime_checkable,
)

from pydantic.fields import FieldInfo

__all__ = [
    "ColumnCodec",
    "CodecRegistry",
    "NoCodecError",
    "ScalarCodec",
    "BoolCodec",
    "EnumCodec",
    "JsonListCodec",
    "default_registry",
    "excel_meta",
    "core_type",
    "is_constant_literal",
]

_NONE_TYPE = type(None)


def _unwrap_annotated(tp: Any) -> Any:
    while get_origin(tp) is typing.Annotated:
        tp = get_args(tp)[0]
    return tp


def core_type(annotation: Any) -> Any:
    """The field's underlying type with ``Annotated[...]`` and an ``Optional``
    (``X | None``) wrapper stripped: ``Annotated[list[int] | None, ...]`` ->
    ``list[int]``. A genuine multi-member union is returned unchanged."""
    tp = _unwrap_annotated(annotation)
    if get_origin(tp) in (Union, types.UnionType):
        non_none = [a for a in get_args(tp) if a is not _NONE_TYPE]
        if len(non_none) == 1:
            return _unwrap_annotated(non_none[0])
    return tp


def is_constant_literal(annotation: Any) -> bool:
    """A single-value ``Literal[...]`` — a fixed constant, not a user choice.
    Such fields are omitted from the sheet entirely (always their one value)."""
    core = core_type(annotation)
    return get_origin(core) is Literal and len(get_args(core)) == 1


def excel_meta(field_info: FieldInfo) -> dict:
    """The field's ``excel`` metadata bag, declared as
    ``Field(json_schema_extra={"excel": {...}})``. Empty dict when absent."""
    extra = field_info.json_schema_extra
    if isinstance(extra, dict):
        meta = extra.get("excel")
        if isinstance(meta, dict):
            return meta
    return {}


class NoCodecError(TypeError):
    """No registered codec claims a field's type (strict: never silently drop)."""


@runtime_checkable
class ColumnCodec(Protocol):
    name: str

    def matches(self, core: Any) -> bool:
        """Whether this codec handles a field whose core type is ``core``."""

    def to_cell(self, value: Any) -> Any:
        """Python value -> a cell-storable value (scalar or string)."""

    def from_cell(self, raw: Any, core: Any) -> Any:
        """Raw cell value -> python value fed to the model (before validation)."""


def _is_blank(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str) and raw.strip() == "":
        return True
    # openpyxl never yields NaN, but guard floats defensively.
    return isinstance(raw, float) and raw != raw


class ScalarCodec:
    """str / int / float and single-choice-among-many ``Literal`` (string) fields.
    Values pass through natively; a blank cell reads back as ``None``."""

    name = "scalar"

    def matches(self, core: Any) -> bool:
        return core in (str, int, float) or get_origin(core) is Literal

    def to_cell(self, value: Any) -> Any:
        return value

    def from_cell(self, raw: Any, core: Any) -> Any:
        return None if _is_blank(raw) else raw


class BoolCodec:
    """Booleans, tolerant of the strings Excel round-trips (TRUE/FALSE/1/0)."""

    name = "bool"
    _TRUE = {"true", "1", "yes", "y"}
    _FALSE = {"false", "0", "no", "n"}

    def matches(self, core: Any) -> bool:
        return core is bool

    def to_cell(self, value: Any) -> Any:
        return value

    def from_cell(self, raw: Any, core: Any) -> Any:
        if _is_blank(raw):
            return None
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in self._TRUE:
            return True
        if s in self._FALSE:
            return False
        return raw  # let pydantic surface the error


class EnumCodec:
    """``enum.Enum`` fields, stored by MEMBER NAME (stable, human-readable, and
    what the field descriptions advertise) rather than by value."""

    name = "enum"

    def matches(self, core: Any) -> bool:
        return isinstance(core, type) and issubclass(core, enum.Enum)

    def to_cell(self, value: Any) -> Any:
        return value.name if isinstance(value, enum.Enum) else value

    def from_cell(self, raw: Any, core: Any) -> Any:
        if _is_blank(raw):
            return None
        if isinstance(raw, core):
            return raw
        name = str(raw).strip()
        try:
            return core[name]  # by member name
        except KeyError:
            return raw  # let pydantic surface the error (maybe it's a value)


class JsonListCodec:
    """A list field stored as a compact JSON array in one cell — comma-safe and
    lossless for ``list[str]`` / ``list[int]`` / ``list[float]`` and, opt-in via
    ``codec="jsonlist"`` metadata, any JSON-encodable list (e.g. a list of nested
    models dumped to dicts). Reconstructs a real ``list`` before validation, so a
    field's own validators receive a list, not a raw string."""

    name = "jsonlist"
    _SCALAR_ELEMS = (str, int, float)

    def matches(self, core: Any) -> bool:
        if get_origin(core) not in (list, typing.List):
            return False
        args = get_args(core)
        return bool(args) and args[0] in self._SCALAR_ELEMS

    def to_cell(self, value: Any) -> Any:
        if value is None:
            return None
        return json.dumps(value, separators=(",", ":"), default=_json_default)

    def from_cell(self, raw: Any, core: Any) -> Any:
        if _is_blank(raw):
            return None
        if isinstance(raw, (list, tuple)):
            return list(raw)
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return raw
        return parsed


def _json_default(obj: Any) -> Any:
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    if isinstance(obj, enum.Enum):
        return obj.name
    raise TypeError(f"cannot JSON-encode {type(obj).__name__}")


class CodecRegistry:
    """Ordered list of codecs; first ``matches`` wins. Register a downstream
    codec with :meth:`register`; it takes precedence over the built-ins for the
    types it claims (prepended), and is also selectable by name via field
    ``excel.codec`` metadata."""

    def __init__(self, codecs: list[ColumnCodec] | None = None) -> None:
        self._codecs: list[ColumnCodec] = list(codecs or [])
        self._by_name: dict[str, ColumnCodec] = {c.name: c for c in self._codecs}

    def register(self, codec: ColumnCodec) -> CodecRegistry:
        self._codecs.insert(0, codec)  # downstream wins over built-ins
        self._by_name[codec.name] = codec
        return self

    def get(self, name: str) -> ColumnCodec:
        try:
            return self._by_name[name]
        except KeyError:
            raise NoCodecError(f"no codec registered under name {name!r}") from None

    def resolve(self, field_info: FieldInfo, field_name: str = "?") -> ColumnCodec:
        meta = excel_meta(field_info)
        named = meta.get("codec")
        if named:
            return self.get(named)
        core = core_type(field_info.annotation)
        for codec in self._codecs:
            if codec.matches(core):
                return codec
        raise NoCodecError(
            f"field {field_name!r}: no codec claims type {core!r}. Register a ColumnCodec for it "
            f'or annotate the field with Field(json_schema_extra={{"excel": {{"codec": "<name>"}}}}).'
        )


def default_registry() -> CodecRegistry:
    """The built-in codecs, in resolution order (bool/enum before scalar so they
    win their types; json-list for scalar lists)."""
    return CodecRegistry([BoolCodec(), EnumCodec(), JsonListCodec(), ScalarCodec()])
