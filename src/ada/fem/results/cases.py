"""The result cases of an FEA result, the same in every format, read one at a time.

A structural check reads a result case by case -- a Sesam SIN of a large model is hundreds of
megabytes, and its cases are read one at a time -- and builds the combinations it is asked for
from the basic cases. :class:`ResultCases` is what it needs to know about the cases and how it
reads them, whatever the format; a reader fills it (``FEAResult.cases``) and
:func:`ada.fem.results.open_result` returns a result that has one.

The fields a case is read with are the role fields of :class:`~.field_data.FieldRole`, in its
layout and convention.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .common import FEAResult
    from .field_data import FieldRole


@dataclass(frozen=True)
class CombinationRecipe:
    """One combination of basic result cases (a Sesam ``RDRESCMB`` record, Results Interface File 4.3.1.11).

    ``terms`` are ``(basic case, FACT, PHASE)``: the basic result case, its factor, and its phase angle
    Φ in RADIANS. Terms whose factor is zero are left out -- they contribute nothing and their case need
    not be read. The same basic case may appear more than once at different phases, and each occurrence is
    its own term.

    ``complex``: whether the COMBINED case is complex (a real and an imaginary part) or real. Whether a
    BASIC case is complex is :attr:`ResultCases.complex_cases`.

    Each term contributes by the RBLODCMB formulae (4.3.1.1, Table 4.1). For a real basic case ``[R]``
    and a complex one ``[R, I]``::

        real combination:     [R cos Φ]·FACT              [R cos Φ − I sin Φ]·FACT
        complex combination:  [R cos Φ, R sin Φ]·FACT     [R cos Φ − I sin Φ, I cos Φ + R sin Φ]·FACT

    So a phase selects which instant of a complex (e.g. quasi-static wave) response enters a real
    design combination.
    """

    complex: bool
    terms: tuple[tuple[int, float, float], ...]

    def __bool__(self) -> bool:
        # An all-zero recipe superposes nothing, and callers have always treated
        # an empty ``{basic: factor}`` dict as "no combination to build".
        return bool(self.terms)

    @property
    def factors(self) -> dict[int, float]:
        """``{basic case: summed factor}``. It ignores the phases, so it describes the combination
        exactly only when every phase is zero and every basic case is real."""
        out: dict[int, float] = {}
        for basic, factor, _phase in self.terms:
            out[basic] = out.get(basic, 0.0) + factor
        return out

    @classmethod
    def coerce(cls, recipe: "CombinationRecipe | dict[int, float]") -> "CombinationRecipe":
        """Accept the legacy ``{basic: factor}`` recipe: a real combination at zero phase."""
        if isinstance(recipe, CombinationRecipe):
            return recipe
        return cls(complex=False, terms=tuple((int(b), float(f), 0.0) for b, f in recipe.items()))


#: Reads basic cases: ``(steps, roles, elements, complex_phase) -> (step, FEAResult)`` per step, in the
#: order asked for, each result holding the fields of ``roles`` (all roles for None) in their role layout.
StepLoader = Callable[[list[int], "frozenset[FieldRole] | None", "frozenset[int] | None", float], Iterator]


@dataclass
class ResultCases:
    """The result cases of an FEA result, and how to read them one at a time.

    ``stored``: the basic cases whose results the file holds (a SIN's RV* steps, Code_Aster's order
    numbers). ``combinations``: cases defined over them (a SIN's ``RDRESCMB``), which a consumer builds by
    superposing the basic cases (:class:`CombinationRecipe`); a combination may also be stored. ``names``:
    a label per case. ``complex_cases``: basic cases whose results are complex (quasi-static wave cases);
    read them at a phase with :meth:`iter_steps`. ``calculation_types``: a Sesam ``ICALTY`` per case where
    the file states one (0 static, 6 quasi-static, ...; for messages). ``load_case_of``: the load case a
    result case was solved for, where it is one load case at factor 1 (the key of the input loads, e.g.
    pressures). ``pressures``: shell pressures per result case and element (the mean of the element's nodal
    intensities, positive along its local z), the imaginary part of a complex case under ``-case``.
    """

    stored: list[int]
    loader: StepLoader
    names: dict[int, str] = field(default_factory=dict)
    combinations: dict[int, CombinationRecipe] = field(default_factory=dict)
    complex_cases: frozenset[int] = frozenset()
    calculation_types: dict[int, int] = field(default_factory=dict)
    load_case_of: dict[int, int] = field(default_factory=dict)
    pressures: Mapping[int, Mapping[int, float]] = field(default_factory=dict)

    @property
    def selectable(self) -> list[int]:
        """Every case a consumer can ask for: the stored ones and the combinations."""
        return sorted(set(self.stored) | set(self.combinations))

    def iter_steps(
        self,
        steps: Iterable[int],
        *,
        roles: Iterable[FieldRole] | None = None,
        elements: Iterable[int] | None = None,
        complex_phase: float = 0.0,
    ) -> Iterator[tuple[int, FEAResult]]:
        """``(step, FEAResult)`` for each stored case of ``steps``, in that order, read one at a time.

        ``roles``: only these role fields (all for None). ``elements``: the beam elements whose
        ``BEAM_FORCES`` are needed (all for None); a reader may leave the others out. ``complex_phase``
        (radians): the phase a complex case is read at, ``R cos Φ − I sin Φ``.
        """
        steps = [int(s) for s in steps]
        missing = sorted(set(steps) - set(self.stored))
        if missing:
            raise KeyError(f"result case(s) {missing} are not stored; stored: {sorted(self.stored)}")
        roles = None if roles is None else frozenset(roles)
        elements = None if elements is None else frozenset(int(e) for e in elements)
        yield from self.loader(steps, roles, elements, float(complex_phase))


__all__ = ["CombinationRecipe", "ResultCases", "StepLoader"]
