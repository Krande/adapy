from typing import TYPE_CHECKING

from ada.fem import Surface
from ada.fem.shell_sides import SHELL_SIDE_NEGATIVE, normalise_shell_side

from ..grammar import format_number
from .helper_utils import get_instance_name, render_block

if TYPE_CHECKING:
    from ada import FEM


def surfaces_str(fem: "FEM", on_assembly_level):
    if len(fem.surfaces) == 0:
        return "** No Surfaces"

    return "\n".join([surface_str(s, on_assembly_level) for s in fem.surfaces.values()])


def shell_face_label(el_f_index) -> str:
    """``SPOS`` / ``SNEG`` for a shell surface's side, through the reading every writer shares.

    ``"SNEG"`` and ``-1`` both give SNEG; a side that names neither face is refused rather than
    written as SPOS (issue #412). One spelling is added here and not in the shared reading: ``0``.
    It is Abaqus' own 0-based first face -- ``SPOS`` is a shell's face 1 -- the writer has always
    taken it as SPOS, and the model zoo holds shell surfaces built that way. The pressure writers
    keep refusing it (Sesam documents SIDE 0 as illegal), so it is not a side in general.
    """
    if el_f_index == 0 and not isinstance(el_f_index, bool):
        return "SPOS"
    side = normalise_shell_side(el_f_index)
    return "SNEG" if side == SHELL_SIDE_NEGATIVE else "SPOS"


def surface_str(surface: Surface, write_on_assembly_level: bool) -> str:
    """Surface assignments str"""
    from ada.fem.elements import find_element_type_from_list
    from ada.fem.shapes import ElemType

    params = [("type", surface.type), ("name", surface.name)]

    if surface.id_refs is not None:
        id_refs_str = "\n".join([f"{m[0]}, {m[1]}" for m in surface.id_refs]).strip()
        return render_block("Surface", params, [id_refs_str])

    if surface.type == surface.TYPES.NODE:
        elem_face_index_label = surface.weight_factor
    else:
        elem_face_index_label = surface.el_face_index

    fs_str = ""
    if not isinstance(surface.fem_set, list):
        f_sets = [surface.fem_set]
        el_face_indices = [elem_face_index_label]
    else:
        f_sets = surface.fem_set
        el_face_indices = elem_face_index_label

    for fs, el_f_index in zip(f_sets, el_face_indices):
        set_ref = get_instance_name(fs, write_on_assembly_level)

        if surface.type == surface.TYPES.NODE:
            # ``set, weight`` -- the weight was never written, so every node surface read back
            # with the default 1.0. Left out only where it IS the default.
            weight = el_f_index
            if weight is not None and float(weight) != 1.0:
                fs_str += f"{set_ref}, {format_number(weight)}\n"
            else:
                fs_str += f"{set_ref}\n"
            continue
        el_type = find_element_type_from_list(fs.members)
        if el_f_index == "":
            # The entry named no face identifier, and that is information. Writing a face
            # number here would invent one, and on a continuum element it would narrow the
            # free-face surface Abaqus reads from a blank side down to a single named face.
            fs_str += f"{set_ref},\n"
            continue
        if el_type == ElemType.SOLID:
            fs_str += f"{set_ref}, S{el_f_index + 1}\n"
        elif el_type == ElemType.SHELL:
            face_str = shell_face_label(el_f_index)
            fs_str += f"{set_ref}, {face_str}\n"
        else:
            raise NotImplementedError()

    return render_block("Surface", params, [fs_str.strip()])
