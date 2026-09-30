"""Restrict a stream reader to chosen result steps, so a bake covers only those."""

from __future__ import annotations

import dataclasses
from typing import Iterable

from .protocol import FEAStreamReader


def normalize_steps(steps: Iterable[int | float]) -> list[float]:
    """``steps`` as a sorted list of distinct step values.

    Empty is allowed and means no step at all: a bake of the geometry only (the
    mesh, beam solids, sets and property fields), for a caller that wants the
    model drawn and no result field with it. ``None``, not an empty list, is
    what asks for every step.
    """
    return sorted({float(s) for s in steps})


def restrict_to_steps(reader: FEAStreamReader, steps: Iterable[int | float]) -> FEAStreamReader:
    """``reader`` narrowed to the steps whose value is in ``steps``.

    A step is named by its VALUE (``FieldSpec.step_values``) -- for a Sesam
    result that is the result-case number (IRES), which is what a caller that
    checks case 12 knows. A reader with a ``select_steps(steps)`` method narrows
    itself, and can then skip reading the other steps at all (the SIN stream
    reader does). Any other reader is wrapped: it still produces every step, and
    the wrapper passes on only the chosen ones, so the output is the same and
    only the time differs.

    Property fields (``category == "property"``: thickness, material, ...) do not
    vary by step and are kept whole. A field that has none of the chosen steps
    is dropped. A step no field has is an error, not a silent omission. No steps
    at all leaves the property fields only.
    """
    wanted = normalize_steps(steps)
    select = getattr(reader, "select_steps", None)
    if callable(select):
        select(wanted)
        return reader
    return StepSubsetReader(reader, wanted)


class StepSubsetReader:
    """A reader wrapper that passes on only the chosen steps of each field.

    Everything but the four field methods is forwarded to the wrapped reader.
    """

    def __init__(self, reader: FEAStreamReader, steps: Iterable[int | float]) -> None:
        self._reader = reader
        self._wanted = set(normalize_steps(steps))
        found: set[float] = set()
        for spec in [*reader.field_specs(), *_element_specs(reader)]:
            if spec.category != "property":
                found.update(float(v) for v in spec.step_values if float(v) in self._wanted)
        missing = sorted(self._wanted - found)
        if missing:
            raise ValueError(f"steps not in this result: {[_step_repr(v) for v in missing]}")

    def select_steps(self, steps: Iterable[int | float]) -> None:
        """Narrow further, to a subset of the steps already chosen."""
        wanted = set(normalize_steps(steps))
        missing = sorted(wanted - self._wanted)
        if missing:
            raise ValueError(f"steps not in this result: {[_step_repr(v) for v in missing]}")
        self._wanted = wanted

    def __getattr__(self, name: str):
        return getattr(self._reader, name)

    def __enter__(self) -> "StepSubsetReader":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._reader.close()

    def _kept(self, spec) -> list[int]:
        return [i for i, v in enumerate(spec.step_values) if float(v) in self._wanted]

    def _narrow(self, spec):
        if spec.category == "property":
            return spec
        kept = self._kept(spec)
        if not kept:
            return None
        return dataclasses.replace(spec, n_steps=len(kept), step_values=[spec.step_values[i] for i in kept])

    def field_specs(self):
        return [n for n in (self._narrow(s) for s in self._reader.field_specs()) if n is not None]

    def element_field_specs(self):
        return [n for n in (self._narrow(s) for s in _element_specs(self._reader)) if n is not None]

    def iter_field_steps(self, field_name: str):
        spec = next((s for s in self._reader.field_specs() if s.name == field_name), None)
        if spec is None:
            raise KeyError(field_name)
        yield from self._pass_on(spec, self._reader.iter_field_steps(field_name))

    def iter_element_field_steps(self, spec):
        inner = next(
            (s for s in _element_specs(self._reader) if s.name == spec.name and s.elem_type == spec.elem_type),
            None,
        )
        if inner is None:
            raise KeyError((spec.name, spec.elem_type))
        yield from self._pass_on(inner, self._reader.iter_element_field_steps(inner))

    def _pass_on(self, inner_spec, values):
        if inner_spec.category == "property":
            yield from values
            return
        position = {i: k for k, i in enumerate(self._kept(inner_spec))}
        for sv in values:
            k = position.get(sv.step_index)
            if k is not None:
                yield dataclasses.replace(sv, step_index=k)


def _element_specs(reader) -> list:
    try:
        return list(reader.element_field_specs())
    except (AttributeError, NotImplementedError):
        return []


def _step_repr(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value
