"""One reading of a shell surface's side, shared by every FEM writer.

A shell surface names one of the element's two faces. adapy carries that in two spellings: the
deck's own label (``"SPOS"`` / ``"SNEG"``) and the ``+1`` / ``-1`` the Abaqus reader normalises
it to. Both are legitimate, and a writer that only recognised one of them wrote a model's
negative face as the positive one with nothing raised (issue #412: the Abaqus writer tested
``== -1`` only, while the Sesam, Calculix and Code_Aster pressure writers also took ``"SNEG"``).

Every writer resolves the side through :func:`normalise_shell_side`, so one ``Surface`` means
one thing in every deck, and a side that is neither is refused rather than defaulted to the
positive face -- an unrecognised value would otherwise be indistinguishable from a real
``SPOS``.
"""

from __future__ import annotations

SHELL_SIDE_POSITIVE = 1
SHELL_SIDE_NEGATIVE = -1

#: Sides that name the face on an element's *negative* normal. The Abaqus reader normalises a
#: single-elset shell surface to ``-1``, a surface built from nodes with ``shell_positive=False``
#: carries the same, and a deck that listed its rows by name carries the label itself.
NEGATIVE_SIDES = frozenset({-1, "SNEG"})

#: ... and the ones that name the face on its positive normal. ``None`` and ``""`` are in here
#: too: a plain element set (or an entry with no face identifier) names no side at all, and
#: Abaqus' own default for a shell surface is SPOS.
POSITIVE_SIDES = frozenset({None, "", 1, "SPOS"})


class UnsupportedShellSide(ValueError):
    """A shell surface side that is neither a positive nor a negative face."""


def normalise_shell_side(side) -> int:
    """``+1`` for the positive face, ``-1`` for the negative one; raise on anything else.

    Accepts ``1`` / ``"SPOS"`` / ``None`` / ``""`` (positive) and ``-1`` / ``"SNEG"`` (negative).
    """
    try:
        if side in POSITIVE_SIDES:
            return SHELL_SIDE_POSITIVE
        if side in NEGATIVE_SIDES:
            return SHELL_SIDE_NEGATIVE
    except TypeError:  # unhashable, e.g. a list: not a side either
        pass
    raise UnsupportedShellSide(
        f"{side!r} is not a shell surface side; expected 1 / 'SPOS' for the positive face "
        f"or -1 / 'SNEG' for the negative one"
    )
