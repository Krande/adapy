"""Element formulations across FE formats: what an element was read as, and what to write it as.

adapy models an element by its *shape* -- a 4-node quad, a 10-node tet. The solver type it was
read as is its *formulation*: ``S4R`` and ``S4`` and ``CPS4`` are all 4-node quads in Abaqus, and
a Sesam ``FQUS`` is another. A writer for the same format as the source writes the formulation
back as it was. A writer for another format cannot, but it should not have to guess blind. So
every element keeps its source :class:`Formulation`, and every writer resolves the type it
writes through :func:`resolve`, in this order:

1. The caller's rules, passed to ``to_fem(..., formulations=...)``: a mapping, a function, or a
   list of them, tried in order (see :data:`FormulationRules`).
2. The source formulation, when it is of the target's own family and a formulation of the
   element's shape there.
3. The target writer's default for the shape.

A source formulation that step 3 replaces is recorded in the conversion report as
``approximated``, counted per (source, written) pair. The shape is exact, but ``S4R`` (reduced
integration) and a full-integration default are not the same element. A type chosen by the
caller's rules is recorded as a ``note``: someone decided it.
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterator, Mapping, Sequence, Union

if TYPE_CHECKING:
    from ada.fem import Elem

#: The naming family of Abaqus's element types. Calculix reads and writes the same names.
ABAQUS = "abaqus"

#: Which writer formats name their types in which family.
FAMILY = {"abaqus": ABAQUS, "calculix": ABAQUS}


@dataclass(frozen=True)
class Formulation:
    """An element type as a format names it: ``Formulation("abaqus", "S4R")``,
    ``Formulation("sesam", "24")``."""

    family: str
    name: str

    def __str__(self) -> str:
        return f"{self.family}:{self.name}"


@functools.lru_cache(maxsize=None)
def formulation(family: str, name: str) -> Formulation:
    """One shared instance per (family, name): a mesh holds one reference per element."""
    return Formulation(family, name)


def as_formulation(value: Any) -> Formulation | None:
    """What an element's formulation slot holds, as a :class:`Formulation`. A bare string is an
    Abaqus type name, the only thing the slot held before it carried a family."""
    if value is None or isinstance(value, Formulation):
        return value
    if isinstance(value, str):
        return formulation(ABAQUS, value.upper())
    return None


#: A mapping's keys may be a :class:`Formulation`, a ``(family, name)`` pair, a bare source
#: name, or the element's shape (the enum or its value); values are the target's type name.
#: A function is called as ``fn(elem, source, target_format)`` and returns the target's type
#: name, or None to leave the element to the next rule.
FormulationRule = Union[Mapping[Any, str], Callable[["Elem", "Formulation | None", str], "str | None"]]
FormulationRules = Union[FormulationRule, Sequence[FormulationRule]]

_rules: contextvars.ContextVar[tuple] = contextvars.ContextVar("ada_formulation_rules", default=())


@contextlib.contextmanager
def use(rules: FormulationRules | None) -> Iterator[None]:
    """Apply ``rules`` to every element written inside the block."""
    if rules is None:
        yield
        return
    seq = tuple(rules) if isinstance(rules, (list, tuple)) else (rules,)
    token = _rules.set(seq)
    try:
        yield
    finally:
        _rules.reset(token)


def _from_rule(rule: FormulationRule, elem: Elem, source: Formulation | None, target: str) -> str | None:
    if callable(rule) and not isinstance(rule, Mapping):
        return rule(elem, source, target)
    keys: list = []
    if source is not None:
        keys += [source, (source.family, source.name), source.name]
    shape = elem.type
    keys += [shape, getattr(shape, "value", shape)]
    for key in keys:
        try:
            if key in rule:
                return rule[key]
        except TypeError:  # an unhashable key in a user's mapping is theirs to fix, not ours
            continue
    return None


def resolve(
    elem: Elem,
    target: str,
    default: Callable[[Any], str],
    accepts: Callable[[Any, str], bool],
    stage: str,
) -> str:
    """The type ``target``'s writer writes ``elem`` as. See the module docstring for the order.

    ``default(shape)`` is the writer's default for a shape; ``accepts(shape, name)`` says whether
    ``name`` is a formulation of that shape in ``target``. A caller's rule naming something
    ``target`` does not accept for the shape is an error, not something to quietly replace.
    """
    from ada.fem.formats import conversion_report

    source = elem.formulation
    shape = elem.type
    for rule in _rules.get():
        chosen = _from_rule(rule, elem, source, target)
        if chosen is None:
            continue
        if not accepts(shape, chosen):
            raise ValueError(f'formulation rule chose "{chosen}" for a {shape}, which is not a {target} type for it')
        if source is not None and source.name != str(chosen).upper():
            conversion_report.current().note(
                stage, "Formulation", f"{source} -> {chosen}", "element formulation chosen by the caller's rule"
            )
        return chosen
    if source is not None and FAMILY.get(target) == source.family and accepts(shape, source.name):
        return source.name
    written = default(shape)
    if source is not None and source.name != str(written).upper():
        conversion_report.current().approximated(
            stage,
            "Formulation",
            f"{source} -> {written}",
            f"no {target} form of the source formulation: written as the default for the shape",
        )
    return written
