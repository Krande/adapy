from __future__ import annotations

from typing import TYPE_CHECKING

from . import Material
from .metals import CarbonSteel

if TYPE_CHECKING:
    from ada import Assembly


def get_material(mat: Material | str, mat_type="metal"):
    if isinstance(mat, Material):
        return mat
    else:
        if mat_type == "metal":
            if mat is None:
                mat = "S355"
            return Material(name=mat, mat_model=CarbonSteel(mat, plasticity_model=None))
        else:
            raise NotImplementedError(f'Material type "{mat_type}" is not yet supported')


def shorten_material_names(assembly: Assembly):
    from ada.core.utils import Counter

    short_suffix = Counter(1)
    for p in assembly.get_all_parts_in_assembly(True):
        for mat in p.materials:
            name_len = 5
            if len(mat.name) > name_len:
                short_mat_name = mat.name[:name_len]
                if short_mat_name in p.materials.name_map.keys():
                    short_mat_name = short_mat_name[:-3] + str(next(short_suffix))
                mat.name = short_mat_name
                p.materials.recreate_name_and_id_maps(p.materials.materials)
