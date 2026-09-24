from typing import TYPE_CHECKING

from ada.fem import Amplitude

from ..grammar import format_number

if TYPE_CHECKING:
    from ada import FEM


def amplitudes_str(fem: "FEM"):
    return "\n".join([amplitude_str(ampl) for ampl in fem.amplitudes.values()])


def amplitude_str(amplitude: Amplitude) -> str:
    """``*Amplitude``: ``x, y`` pairs, four to a data line. Exact numbers -- this wrote them at
    ``{:.4E}``, five significant digits."""
    name, x, y, smooth = amplitude.name, amplitude.x, amplitude.y, amplitude.smooth
    pairs = [f"{format_number(a)}, {format_number(b)}" for a, b in zip(list(x), list(y))]
    lines = [", ".join(pairs[i : i + 4]) for i in range(0, len(pairs), 4)]
    smooth = f", DEFINITION=TABULAR, SMOOTH={format_number(smooth)}" if smooth is not None else ""
    data = ",\n".join(f"         {line}" for line in lines)
    return f"*Amplitude, name={name}{smooth}\n{data}"
