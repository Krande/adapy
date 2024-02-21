from ada.core.utils import NewLine
from ada.fem import FieldOutput, HistOutput

from .helper_utils import get_instance_name


def hist_output_str(hist_output: HistOutput) -> str:
    hist_map = dict(
        connector="*Element Output, elset=",
        node="*Node Output, nset=",
        energy="*Energy Output",
        contact="*Contact Output",
    )

    if hist_output.type not in hist_map.keys():
        raise Exception('Unknown output type "{}"'.format(hist_output.type))

    set_type_str = hist_map[hist_output.type]
    newline = NewLine(10)
    var_str = "".join([" {},".format(val) + next(newline) for val in hist_output.variables])[:-1]

    if hist_output.type == HistOutput.TYPES.CONTACT:
        iname1 = get_instance_name(hist_output.fem_set[1], True)
        iname2 = get_instance_name(hist_output.fem_set[0], True)
        fem_set_str = f", master={iname1}, slave={iname2}"
    else:
        if hist_output.fem_set is None:
            fem_set_str = ""
        else:
            instance_name = get_instance_name(hist_output.fem_set, True)
            if hist_output.type in (HistOutput.TYPES.ENERGY, HistOutput.TYPES.CONTACT):
                fem_set_str = f", elset={instance_name}"
            else:
                fem_set_str = instance_name

    return f"""*Output, history, {hist_output.int_type}={hist_output.int_value}
** HISTORY OUTPUT: {hist_output.name}
**
{set_type_str}{fem_set_str}
{var_str}"""


def field_output_str(field_output: FieldOutput) -> str:
    if len(field_output.nodal) > 0:
        nodal_str = "*Node Output\n "
        nodal_str += ", ".join([str(val) for val in field_output.nodal])
    else:
        nodal_str = "** No Nodal Output"

    if len(field_output.element) > 0:
        element_str = "*Element Output, directions=YES\n "
        element_str += ", ".join([str(val) for val in field_output.element])
    else:
        element_str = "** No Element Output"

    if len(field_output.contact) > 0:
        contact_str = "*Contact Output\n "
        contact_str += ", ".join([str(val) for val in field_output.contact])
    else:
        contact_str = "** No Contact Output"
    return f"""** FIELD OUTPUT: {field_output.name}
**
*Output, field, {field_output.int_type}={field_output.int_value}
{nodal_str}
{element_str}
{contact_str}""".strip()
