from dataclasses import dataclass

from ada.fem.formats.abaqus.elem_formulations import AbaqusDefaultElemTypes


class CalculixOptions:
    def __init__(self):
        self.default_elements = AbaqusDefaultElemTypes(is_calculix_variant=True)
        #: CalculiX has no tension-only truss (and no ``*No Compression``). A cable section is refused unless this is
        #: set, when it is written as a truss -- which carries compression too -- and reported as approximated.
        self.cable_as_truss = False


@dataclass
class CalculixStepOptions:
    """``*DYNAMIC`` parameters of an implicit dynamic step (CalculiX manual, 7.32).

    ``alpha`` is the Hilber-Hughes-Taylor parameter, in [-1/3, 0]: None leaves ccx's default (-0.05), more negative
    damps the high frequencies harder. ``direct`` fixes the increment at the step's initial increment (``DIRECT``):
    what a coupled run needs, where the coupling library sets the time step and ccx must not cut it.
    """

    alpha: float | None = None
    direct: bool = False
