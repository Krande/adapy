from typing import Union

from . import Material
from .metals import CarbonSteel


def get_material(mat: Union[Material, str], mat_type="metal"):
    if isinstance(mat, Material):
        return mat
    else:
        if mat_type == "metal":
            if mat is None:
                mat = "S355"
            return Material(name=mat, mat_model=CarbonSteel(mat, plasticity_model=None))
        else:
            raise NotImplementedError(f'Material type "{mat_type}" is not yet supported')
