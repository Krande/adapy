"""Concept names in a Code_Aster command file.

A ``.comm`` file is Python: every load, Bc, coupling and material becomes a *concept*, a Python name
bound to the result of a command (``grav = AFFE_CHAR_MECA(...)``). The writer used to bind the user's
own name, so a name that is not an identifier broke the file and one the deck uses itself rebound it.
Measured with Code_Aster 18.1.8 on a cantilever shell under gravity: a load or Bc named ``model``
rebound the ``AFFE_MODELE`` concept and the run stopped at ``<SUPERVIS_4>``; a load named ``my grav``
stopped it at ``<F>_SYNTAX_ERROR``; a load named ``result`` analysed only because ``MECA_STATIQUE``'s
arguments happen to be evaluated before the rebinding. Two objects of one name (two parts' Bc "fix")
were one concept: the second replaced the first.

A concept name is internal to the deck -- results are read back by field and case name, never by a
load's concept -- so nothing has to keep the user's name verbatim. :class:`ConceptNames` gives each
object a name of its own, ``<prefix><sanitised name>``, with ``_2``, ``_3``, ... when that is one of
the deck's own names (:func:`is_reserved`), a keyword, or already another object's. One registry serves
one command file (:func:`deck`), so the names are deterministic: the same model writes the same file.
"""

from __future__ import annotations

import contextlib
import contextvars
import keyword
import re
from typing import Iterator

from ada.fem.formats import conversion_report

#: The conversion-report stage of this writer.
STAGE = "code_aster writer"

#: The names the command file binds itself (``mesh = LIRE_MAILLAGE(...)``, ``CO('stiff')``, the
#: Python helpers it assigns). A user object never gets one of these. Kept complete by
#: ``tests/core/fem/formats/code_aster/test_concept_names.py``, which scans the writer's templates.
RESERVED = frozenset(
    {
        # the model, its mesh and what is assigned to it
        "mesh",
        "mesh_ref",
        "ma_tri6",
        "ma_quad8",
        "model",
        "model_0",
        "material",
        "element",
        "nl_mat",
        "Traction",
        # the element groups the model is assigned over
        "bm_sets",
        "sh_sets",
        "sh_2nd_order_sets",
        "so_sets",
        # a Bc's dof dictionary
        "dofs",
        # results and post-processing
        "result",
        "stress",
        "strain",
        "strainP",
        "timeReel",
        "timeInst",
        "bc_step",
        # the charge holding every support (write_bc.SUPPORTS) and the one holding the prescribed dofs at
        # zero (write_bc.PRESCRIBED_AT_ZERO)
        "supports",
        "prescribed_zero",
        # eigen analysis
        "modes",
        "modes_0",
        "tab_modes",
        "dofs_eig",
        "stiff",
        "mass",
    }
)

#: The names the command file makes up as it goes: a static step's result (``result``, ``result2``, ...) and
#: what the step binds after it -- its charge of prescribed values (``result_pd``), the instants and multiplier
#: functions of its cases (``result_t``, ``result_f1``, ``result_g1``) and the unit charge of a prescribed dof
#: (``result_p1``); a second-order shell face's stresses (``result_sup``, ``result_inf``); and the supports of a
#: step with supports of its own, named after its position in the deck (``supports_2``, ``prescribed_zero_2``).
#: Kept complete by the same test.
RESERVED_PATTERNS = (
    re.compile(r"result\d*"),
    re.compile(r"result\d*_(?:pd|t|f\d+|p\d+|g\d+|sup|inf)"),
    re.compile(r"supports_\d+"),
    re.compile(r"prescribed_zero_\d+"),
)


def is_reserved(name: str) -> bool:
    """Whether the command file may bind ``name`` itself (:data:`RESERVED`, :data:`RESERVED_PATTERNS`)."""
    return name in RESERVED or any(p.fullmatch(name) for p in RESERVED_PATTERNS)


#: The prefix of each kind of concept a user object becomes. None of the deck's own names starts with
#: ``ld_``, ``cp_`` or ``mt_``; ``bc_step`` does, and is in :data:`RESERVED`.
PREFIX = {"load": "ld_", "bc": "bc_", "coupling": "cp_", "material": "mt_"}

_current: contextvars.ContextVar["ConceptNames | None"] = contextvars.ContextVar("code_aster_concepts", default=None)


def _sanitised(name: str) -> str:
    return re.sub(r"\W", "_", str(name), flags=re.ASCII) or "_"


class ConceptNames:
    """The concept names of one command file.

    An object keeps the name it was first given; a load, Bc or coupling is told apart from another of
    the same name by identity. A material is keyed by its name, as the writer writes one
    ``DEFI_MATERIAU`` per material name."""

    def __init__(self):
        self._by_key: dict = {}
        self._taken: set[str] = set()

    def name(self, obj, kind: str) -> str:
        key = (kind, obj.name) if kind == "material" else (kind, id(obj))
        hit = self._by_key.get(key)
        if hit is not None:
            return hit[1]
        prefix = PREFIX[kind]
        base = prefix + _sanitised(obj.name)
        candidate, n = base, 2
        while is_reserved(candidate) or candidate in self._taken or keyword.iskeyword(candidate):
            candidate = f"{base}_{n}"
            n += 1
        self._taken.add(candidate)
        # The object is held, not just its id: an id is only unique while its object lives.
        self._by_key[key] = (obj, candidate)
        if candidate != prefix + str(obj.name):
            conversion_report.current().note(
                STAGE,
                kind,
                obj.name,
                "named differently in the Code_Aster command file (a concept name is a Python identifier of its "
                "own there)",
                concept=candidate,
            )
        return candidate


@contextlib.contextmanager
def deck() -> Iterator[ConceptNames]:
    """The registry of one command file, for every writer function called inside."""
    token = _current.set(ConceptNames())
    try:
        yield _current.get()
    finally:
        _current.reset(token)


def current() -> ConceptNames:
    """The registry of the command file being written; a writer function called on its own (outside
    :func:`deck`) gets a registry of its own."""
    registry = _current.get()
    return registry if registry is not None else ConceptNames()


def concept_name(obj, kind: str) -> str:
    return current().name(obj, kind)
