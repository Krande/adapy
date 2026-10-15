from ada.fem.formats.abaqus.elem_formulations import AbaqusDefaultElemTypes


class CalculixOptions:
    def __init__(self):
        self.default_elements = AbaqusDefaultElemTypes(is_calculix_variant=True)
        #: CalculiX has no tension-only truss (and no ``*No Compression``). A cable section is refused unless this is
        #: set, when it is written as a truss -- which carries compression too -- and reported as approximated.
        self.cable_as_truss = False
