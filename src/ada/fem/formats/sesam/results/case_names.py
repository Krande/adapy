"""What a Sesam deck calls its result cases.

A step in the viewer is labelled with its VALUE — "1", "2", "3" — because that is
all the artefact pipeline knows about it. The deck knows more: it names its load
cases (`girder_local`, `unit_acc_x`, `lcc1`), and those names are how an engineer
recognises which case they are looking at. "Case 3" is an index; "Case 3
(unit_acc_x)" is a load case.

The names were already reachable — codecheck's viewer export reads exactly these
records when it writes its own case labels — but only after a code check had run.
Anything that wanted them BEFORE one (a picker for choosing which cases to check)
had nothing but the numbers.

Two records, in the precedence codecheck established:

* ``TDRESREF`` names a RESULT case, and wins where present.
* ``TDLOAD`` names a LOAD case, and fills in where the deck named no result case
  — which is the common shape for a static deck, where the two coincide.

``RDRESCMB`` defines the COMBINATIONS on top of those, and names nothing: a
combination's name, when it has one, is in ``TDRESREF`` like any other result
case. What ``RDRESCMB`` adds is the recipe — which basic cases at which factors —
and :func:`combination_label` renders it for a caller that wants to show how a
case is built up. That is supporting detail, not the case's name.
"""

from __future__ import annotations

from typing import Any

__all__ = ["combination_label", "result_case_names", "selectable_result_cases"]


def _factor(value: float) -> str:
    """A load factor as an engineer writes it: ``1.2``, not ``1.2000000476``."""
    # The SIN stores factors as float32, so the double they widen to is never
    # exactly the decimal that was typed. Round before formatting or every
    # factor carries eight digits of float noise.
    return f"{round(float(value), 4):g}"


def combination_label(components: dict[int, float], names: dict[int, str]) -> str:
    """Describe a combination by its recipe: ``1.2·girder_local + 1.1·deck``.

    ``components`` is ``{basic case: factor}`` as ``read_result_combinations``
    returns it; ``names`` is what the basic cases are called. A basic case with
    no name of its own appears as ``case 5`` — better than dropping the term,
    which would silently misdescribe the combination.

    Ordered by case number rather than by factor, so the same basics always read
    in the same order across combinations and two of them can be compared by eye.
    Never truncated: this is the detail view of a case, not its label.
    """
    if not components:
        return ""
    return " + ".join(
        f"{_factor(f)}\u00b7{names.get(n) or f'case {n}'}" for n, f in sorted(components.items())
    )


def _text_records(sin_file: Any, card: str) -> dict[int, str]:
    """``{case number: text}`` from one TD* card, or empty when it has none."""
    if card not in sin_file.type_blocks:
        return {}
    out: dict[int, str] = {}
    for prefix, text in sin_file.iter_text_records(card):
        if not prefix or not text:
            continue
        name = str(text).strip()
        if name:
            out[int(round(prefix[0]))] = name
    return out


def result_case_names(sin_file: Any) -> dict[int, str] | None:
    """``{case number: name}`` for a Sesam deck, or ``None`` when it names none.

    Takes the open file rather than a reader: these are text records, and the
    file iterates them directly without the reader's numeric-record machinery.
    """
    if sin_file is None or not getattr(sin_file, "type_blocks", None):
        return None
    try:
        # Load cases first, so the result-case pass overwrites them rather than
        # the other way round.
        names = _text_records(sin_file, "TDLOAD")
        names.update(_text_records(sin_file, "TDRESREF"))
    except Exception:  # a deck we cannot read here is a deck with no names
        return None
    return names or None


def selectable_result_cases(sin_file: Any) -> list[dict] | None:
    """Every result case the deck offers, named, or ``None`` when it offers none.

    Distinct from the manifest's field STEPS, which are the cases actually stored
    as RV* records. On a "smart load combination" deck those are only the basic
    cases: the design cases — the ones anyone runs a check for — are defined by
    ``RDRESCMB`` and stored nowhere, so a picker built from steps offers exactly
    the cases the engineer does not want and omits the two they do.

    A combination carries ``makeup``, its recipe over the basic cases. It is
    supporting detail for a UI to show on demand, deliberately kept out of
    ``name``: the case is called ``lcc1``, and a label that spelled out five
    weighted terms instead would be unreadable everywhere a case is listed.
    """
    from ada.fem.formats.sesam.results.read_sin import read_result_combinations

    if sin_file is None or not getattr(sin_file, "type_blocks", None):
        return None
    names = result_case_names(sin_file) or {}
    try:
        combinations = read_result_combinations(sin_file)
    except Exception:
        combinations = {}
    numbers = sorted(set(names) | set(combinations))
    if not numbers:
        return None
    out: list[dict] = []
    for n in numbers:
        entry: dict = {"n": n}
        if names.get(n):
            entry["name"] = names[n]
        if n in combinations:
            entry["combination"] = True
            makeup = combination_label(combinations[n], names)
            if makeup:
                entry["makeup"] = makeup
        out.append(entry)
    return out
