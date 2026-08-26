"""What a Sesam deck calls its result cases.

A step in the viewer is labelled with its VALUE — "1", "2", "3" — because that is
all the artefact pipeline knows about it. The deck knows more: it names its load
cases (`girder_local`, `unit_acc_x`, `deck`), and those names are how an engineer
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
"""

from __future__ import annotations

from typing import Any

__all__ = ["result_case_names"]


def _load_case_names(sin_file: Any) -> dict[int, str]:
    """Map load case number → name from ``TDLOAD``."""
    if "TDLOAD" not in sin_file.type_blocks:
        return {}
    out: dict[int, str] = {}
    for prefix, text in sin_file.iter_text_records("TDLOAD"):
        if not prefix or not text:
            continue
        out[int(round(prefix[0]))] = str(text).strip()
    return out


def result_case_names(sin_file: Any) -> dict[int, str] | None:
    """``{case number: name}`` for a Sesam deck, or ``None`` when it names none.

    Takes the open file rather than a reader: these are text records, and the
    file iterates them directly without the reader's numeric-record machinery.
    """
    from ada.fem.formats.sesam.results.read_sin import read_result_names

    if sin_file is None or not getattr(sin_file, "type_blocks", None):
        return None

    try:
        # Load cases first, so the result-case pass overwrites them rather than
        # the other way round.
        names = _load_case_names(sin_file)
        names.update({k: str(v).strip() for k, v in read_result_names(sin_file).items() if v})
    except Exception:  # a deck we cannot read here is a deck with no names
        return None
    return {k: v for k, v in names.items() if v} or None
