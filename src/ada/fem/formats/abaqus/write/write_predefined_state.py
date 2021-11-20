from typing import TYPE_CHECKING

from ada.fem import PredefinedField

from .helper_utils import get_instance_name

if TYPE_CHECKING:
    from ada import FEM


def predefined_fields_str(fem: "FEM"):
    def eval_fields(pre_field: PredefinedField):
        return True if pre_field.type != PredefinedField.TYPES.INITIAL_STATE else False

    return "\n".join(
        [predefined_field_str(prefield) for prefield in filter(eval_fields, fem.predefined_fields.values())]
    )


def predefined_field_str(pre_field: PredefinedField) -> str:
    dofs_str = ""
    for dof, magn in zip(pre_field.dofs, pre_field.magnitude):
        if float(magn) == 0.0:
            continue
        dofs_str += f"{get_instance_name(pre_field.fem_set, True)}, {dof}, {magn}\n"
    dofs_str.rstrip()
    return f"""** PREDEFINED FIELDS
**
** Name: {pre_field.name}   Type: {pre_field.type}
*Initial Conditions, type={pre_field.type}
{dofs_str}"""
