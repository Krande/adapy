"""The linear solver of a Code_Aster step (``SOLVEUR``, U4.50.01), chosen by ``options.CODE_ASTER.solver``."""

from __future__ import annotations

from ada.fem.exceptions.element_support import IncompatibleElements

#: The direct solvers ``SOLVEUR=_F(METHODE=...)`` takes without further keywords from adapy.
METHODS = ("MUMPS", "MULT_FRONT", "LDLT")


def solveur_str(method: str, resi_rela: float | None = None) -> str:
    """``SOLVEUR=_F(METHODE=...)`` as a keyword line of a solve command; ``RESI_RELA`` for MUMPS only (the others
    have no such check)."""
    method = method.upper()
    if method not in METHODS:
        raise IncompatibleElements(f"code_aster writer: solver {method!r} is none of {METHODS}")
    rr = f", RESI_RELA={resi_rela!r}" if method == "MUMPS" and resi_rela is not None else ""
    return f"\n    SOLVEUR=_F(METHODE='{method}'{rr}),"
