from ada.fem.formats.abaqus.elem_formulations import AbaqusDefaultElemTypes


class CalculixOptions:
    def __init__(self):
        self.default_elements = AbaqusDefaultElemTypes(is_calculix_variant=True)
