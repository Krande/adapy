from typing import TYPE_CHECKING

from ada.fem import PredefinedField

from ..grammar import format_number, render_keyword
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
    # Every DOF the field names, zeros included: a zero Abaqus would assume anyway, but leaving it
    # out read back as a field on fewer DOFs than the one written.
    set_ref = get_instance_name(pre_field.fem_set, True)
    lines = [f"{set_ref}, {dof}, {format_number(magn)}" for dof, magn in zip(pre_field.dofs, pre_field.magnitude)]
    comments = ["PREDEFINED FIELDS", "", f"Name: {pre_field.name}   Type: {pre_field.type}"]
    return render_keyword("Initial Conditions", [("type", pre_field.type)], lines, comments)
