"""What kind of analysis each field of a MED result file holds: ``"static"``, ``"eigen"`` or ``"transient"``.

A MED field stores its steps as (``NDT``, ``NOR``, ``PDT``) triples, and nothing in a step says whether ``PDT`` is a
time or an eigenfrequency: Code_Aster writes a mode shape's frequency there (measured, 18.1.7: a ``CALC_MODES``
result prints ``PDT`` 4.673, 6.171, 18.518 Hz with ``NDT = NOR`` = the mode number) exactly as it writes an instant of
a ``STAT_NON_LINE`` or ``DYNA_VIBRA`` result. Neither do the field's own attributes (its time unit ``UNT`` is empty in
both). So the step values cannot tell a mode from a time, and guessing from them -- "positive and ascending is a
mode" -- turned every time series whose first stored instant is not 0 into mode shapes.

The kind is taken from positive evidence, in this order:

1. the command file beside the result (``<name>.comm``, which adapy's writer always writes and a Code_Aster study
   usually keeps): the command that made each result concept -- ``CALC_MODES`` and the other modal solvers make
   modes, ``DYNA_*`` and ``STAT_NON_LINE`` time series, ``MECA_STATIQUE`` and ``MACRO_ELAS_MULT`` static results. A
   MED field's name is its concept's name padded to 8 characters with ``_``, then the field (``modes___DEPL``);
2. the concept's name, when it says modes (``modes___DEPL``, adapy's own for an eigen step, or any concept named
   ``mode...``);
3. else the steps: one step, or Code_Aster's load-case marker ``PDT = 999.999`` on every step, is static; several
   steps are a time series, whatever instant it starts at -- which is also how a MED file from another tool
   (medcoupling, no Code_Aster naming at all) is read.
"""

from __future__ import annotations

import pathlib
import re
from typing import Iterable, Literal

AnalysisKind = Literal["static", "eigen", "transient"]

#: The time (``PDT``) Code_Aster prints for every field of a result without instants (``MACRO_ELAS_MULT``).
NO_INSTANT = 999.999

#: Code_Aster commands that make a modal result (``mode_meca``).
MODAL_COMMANDS = frozenset(
    {
        "CALC_MODES",
        "MODE_ITER_SIMULT",
        "MODE_ITER_INV",
        "MODE_ITER_CYCL",
        "MODE_STATIQUE",
        "MACRO_MODE_MECA",
        "CALC_MODE_ROTATION",
        "MODE_NON_LINE",
    }
)
#: ... and those that make an evolution in time (``evol_noli``, ``dyna_trans``, ``evol_elas`` with instants).
TRANSIENT_COMMANDS = frozenset(
    {"STAT_NON_LINE", "DYNA_NON_LINE", "DYNA_VIBRA", "DYNA_LINE", "DYNA_TRAN_MODAL", "DYNA_LINE_TRAN"}
)
#: ... and those that make static results (by instant or by load case).
STATIC_COMMANDS = frozenset({"MECA_STATIQUE", "MACRO_ELAS_MULT", "MECA_STATIQUE_MULT"})

_CONCEPT = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*([A-Z_][A-Z0-9_]*)\s*\(", re.M)


def concept_of(field_name: str) -> str:
    """The result concept a MED field belongs to: its name's first 8 characters, padding stripped
    (``modes___DEPL`` -> ``modes``, ``result2_DEPL`` -> ``result2``). Fields of ``IMPR_RESU``'s ``NOM_CHAM_MED``
    have names of their own and no concept (``""``)."""
    if len(field_name) <= 8:
        return ""
    return field_name[:8].rstrip("_")


def concept_commands(comm_text: str) -> dict[str, str]:
    """``{concept: command}`` for every concept a command file binds to a command call. A later binding to a command
    of no known kind does not replace one of a known kind: ``result = STAT_NON_LINE(...)`` then
    ``result = CALC_CHAMP(reuse=result, ...)``, or ``modes = CALC_MODES(...)`` then ``modes = NORM_MODE(reuse=modes)``,
    add to the concept rather than make a new one."""
    out: dict[str, str] = {}
    for concept, command in _CONCEPT.findall(comm_text):
        if concept in out and kind_of_command(command) is None and kind_of_command(out[concept]) is not None:
            continue
        out[concept] = command
    return out


def kind_of_command(command: str | None) -> AnalysisKind | None:
    if command is None:
        return None
    if command in MODAL_COMMANDS:
        return "eigen"
    if command in TRANSIENT_COMMANDS:
        return "transient"
    if command in STATIC_COMMANDS:
        return "static"
    return None


def field_kind(field_name: str, step_values: Iterable[float], commands: dict[str, str] | None = None) -> AnalysisKind:
    """The kind of one MED field (see the module docstring for the order of the evidence)."""
    concept = concept_of(field_name)
    by_command = kind_of_command((commands or {}).get(concept))
    values = [float(v) for v in step_values]
    if by_command == "transient" and len(values) <= 1:
        return "static"  # a nonlinear static result of one instant has nothing to play
    if by_command is not None:
        return by_command
    if concept.lower().startswith("mode"):
        return "eigen"
    if len(values) <= 1 or all(abs(v - NO_INSTANT) < 1e-9 for v in values):
        return "static"
    return "transient"


def companion_commands(med_file: str | pathlib.Path) -> dict[str, str]:
    """The concept commands of the command file beside ``med_file`` (same name, ``.comm``), or ``{}``."""
    comm = pathlib.Path(med_file).with_suffix(".comm")
    if not comm.is_file():
        return {}
    try:
        return concept_commands(comm.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def result_kind(kinds: Iterable[AnalysisKind]) -> AnalysisKind | None:
    """One kind for a whole result: the fields' kind when they agree, else None."""
    distinct = set(kinds)
    return distinct.pop() if len(distinct) == 1 else None
