"""Checking a run's parameters against the provider's JSON Schema, without a schema library.

The API validates a request before anything is queued, and the API image carries no
``jsonschema`` package, so the part of JSON Schema a parameter form needs is implemented here:

* ``type`` (``string``, ``number``, ``integer``, ``boolean``, ``object``, ``array``, ``null``, or
  a list of them), ``enum``, ``const``;
* numbers: ``minimum``, ``maximum``, ``exclusiveMinimum``, ``exclusiveMaximum``;
* strings: ``minLength``, ``maxLength``, ``pattern``;
* objects: ``properties``, ``required``, ``additionalProperties`` (a boolean or a schema);
* arrays: ``items``, ``minItems``, ``maxItems``;
* ``default``: filled in for a property the request leaves out.

Anything else in a schema (``title``, ``description``, ``format``, ``$ref``, ``oneOf``, ...) is
not checked. A provider that needs more validates in ``run`` and fails the run with its own
message.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Mapping

__all__ = ["ParameterError", "validate_parameters"]


class ParameterError(ValueError):
    """The parameters do not fit the schema. ``problems`` lists each one, with its path."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def _is_number(value: Any) -> bool:
    # bool is an int in Python and is not a number in JSON.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


_TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "number": _is_number,
    "integer": lambda v: _is_number(v) and float(v).is_integer(),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, Mapping),
    "array": lambda v: isinstance(v, (list, tuple)),
    "null": lambda v: v is None,
}


def _at(path: str) -> str:
    return path or "parameters"


def _check(value: Any, schema: Any, path: str, problems: list[str]) -> Any:
    """Validate ``value`` and return it with defaults filled in. Problems are collected, not
    raised, so one request reports everything wrong with it."""
    if not isinstance(schema, Mapping):
        return value

    wanted = schema.get("type")
    if wanted is not None:
        names = [wanted] if isinstance(wanted, str) else [t for t in wanted if isinstance(t, str)]
        if names and not any(_TYPE_CHECKS.get(n, lambda _v: True)(value) for n in names):
            problems.append(f"{_at(path)}: expected {' or '.join(names)}, got {type(value).__name__}")
            return value
    if "enum" in schema and value not in list(schema["enum"]):
        problems.append(f"{_at(path)}: must be one of {list(schema['enum'])!r}")
        return value
    if "const" in schema and value != schema["const"]:
        problems.append(f"{_at(path)}: must be {schema['const']!r}")
        return value

    if _is_number(value):
        for key, bad, word in (
            ("minimum", lambda limit: value < limit, "at least"),
            ("maximum", lambda limit: value > limit, "at most"),
            ("exclusiveMinimum", lambda limit: value <= limit, "greater than"),
            ("exclusiveMaximum", lambda limit: value >= limit, "less than"),
        ):
            limit = schema.get(key)
            if _is_number(limit) and bad(limit):
                problems.append(f"{_at(path)}: must be {word} {limit}")
    elif isinstance(value, str):
        if _is_number(schema.get("minLength")) and len(value) < schema["minLength"]:
            problems.append(f"{_at(path)}: must be at least {schema['minLength']} characters")
        if _is_number(schema.get("maxLength")) and len(value) > schema["maxLength"]:
            problems.append(f"{_at(path)}: must be at most {schema['maxLength']} characters")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                if re.search(pattern, value) is None:
                    problems.append(f"{_at(path)}: does not match {pattern!r}")
            except re.error:
                # A pattern Python cannot compile is the provider's mistake, not the caller's.
                pass
    elif isinstance(value, Mapping):
        return _check_object(value, schema, path, problems)
    elif isinstance(value, (list, tuple)):
        if _is_number(schema.get("minItems")) and len(value) < schema["minItems"]:
            problems.append(f"{_at(path)}: needs at least {schema['minItems']} items")
        if _is_number(schema.get("maxItems")) and len(value) > schema["maxItems"]:
            problems.append(f"{_at(path)}: takes at most {schema['maxItems']} items")
        items = schema.get("items")
        if isinstance(items, Mapping):
            return [_check(v, items, f"{path}[{i}]", problems) for i, v in enumerate(value)]
        return list(value)
    return value


def _check_object(value: Mapping, schema: Mapping, path: str, problems: list[str]) -> dict:
    properties = schema.get("properties")
    properties = properties if isinstance(properties, Mapping) else {}
    out: dict[str, Any] = {}
    for name, sub in properties.items():
        child = f"{path}.{name}" if path else str(name)
        if name in value:
            out[name] = _check(value[name], sub, child, problems)
        elif isinstance(sub, Mapping) and "default" in sub:
            out[name] = copy.deepcopy(sub["default"])
    for name in schema.get("required") or []:
        if name not in out and name not in value:
            problems.append(f"{f'{path}.{name}' if path else name}: is required")
    extra = schema.get("additionalProperties", True)
    for name, item in value.items():
        if name in properties:
            continue
        child = f"{path}.{name}" if path else str(name)
        if extra is False:
            problems.append(f"{child}: is not a parameter of this provider")
        else:
            out[name] = _check(item, extra, child, problems) if isinstance(extra, Mapping) else item
    return out


def validate_parameters(schema: Mapping[str, Any] | None, parameters: Any) -> dict[str, Any]:
    """``parameters`` checked against ``schema``, with the schema's defaults filled in.

    Raises :class:`ParameterError` naming every problem. An empty or absent schema accepts any
    object. The parameters must be a JSON object whatever the schema says.
    """
    if parameters is None:
        parameters = {}
    if not isinstance(parameters, Mapping):
        raise ParameterError([f"parameters: expected object, got {type(parameters).__name__}"])
    problems: list[str] = []
    result = _check(parameters, schema or {}, "", problems)
    if problems:
        raise ParameterError(problems)
    return dict(result)
