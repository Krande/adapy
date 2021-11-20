from typing import TYPE_CHECKING

from ada.fem import Amplitude

if TYPE_CHECKING:
    from ada import FEM


def amplitudes_str(fem: "FEM"):
    return "\n".join([amplitude_str(ampl) for ampl in fem.amplitudes.values()])


def amplitude_str(amplitude: Amplitude) -> str:
    name, x, y, smooth = amplitude.name, amplitude.x, amplitude.y, amplitude.smooth
    a = 1
    data = ""
    for i, var in enumerate(zip(list(x), list(y))):
        if a == 4:
            if i == len(list(x)) - 1:
                data += "{:.4E}, {:.4E}, ".format(var[0], var[1])
            else:
                data += "{:.4E}, {:.4E},\n         ".format(var[0], var[1])
            a = 0
        else:
            data += "{:.4E}, {:.4E}, ".format(var[0], var[1])
        a += 1

    smooth = ", DEFINITION=TABULAR, SMOOTH={}".format(smooth) if smooth is not None else ""
    amplitude = """*Amplitude, name={0}{2}\n         {1}\n""".format(name, data, smooth)
    return amplitude.rstrip()
