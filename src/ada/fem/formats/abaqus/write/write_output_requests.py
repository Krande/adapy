from ada.core.utils import NewLine
from ada.fem import FieldOutput, HistOutput

from ..grammar import render_keyword
from .helper_utils import get_instance_name, render_block


def hist_output_str(hist_output: HistOutput) -> str:
    hist_map = dict(
        connector=("Element Output", "elset"),
        node=("Node Output", "nset"),
        energy=("Energy Output", None),
        contact=("Contact Output", None),
    )

    if hist_output.type not in hist_map.keys():
        raise Exception('Unknown output type "{}"'.format(hist_output.type))

    keyword, set_param = hist_map[hist_output.type]
    newline = NewLine(10)
    var_str = "".join([" {},".format(val) + next(newline) for val in hist_output.variables])[:-1]

    if hist_output.type == HistOutput.TYPES.CONTACT:
        iname1 = get_instance_name(hist_output.fem_set[1], True)
        iname2 = get_instance_name(hist_output.fem_set[0], True)
        params = [("master", iname1), ("slave", iname2)]
    elif hist_output.fem_set is None:
        params = [] if set_param is None else [(set_param, "")]
    else:
        params = [(set_param or "elset", get_instance_name(hist_output.fem_set, True))]

    return render_keyword("Output", [("history", None), (hist_output.int_type, hist_output.int_value)]) + render_block(
        keyword, params, [var_str], [f"HISTORY OUTPUT: {hist_output.name}", ""]
    )


def field_output_str(field_output: FieldOutput) -> str:
    def variables(keyword: str, values, params=()) -> str:
        return render_block(keyword, params, [" " + ", ".join(str(val) for val in values)])

    if len(field_output.nodal) > 0:
        nodal_str = variables("Node Output", field_output.nodal)
    else:
        nodal_str = "** No Nodal Output"

    if len(field_output.element) > 0:
        element_str = variables("Element Output", field_output.element, [("directions", "YES")])
    else:
        element_str = "** No Element Output"

    if len(field_output.contact) > 0:
        contact_str = variables("Contact Output", field_output.contact)
    else:
        contact_str = "** No Contact Output"
    output = render_block(
        "Output",
        [("field", None), (field_output.int_type, field_output.int_value)],
        (),
        [f"FIELD OUTPUT: {field_output.name}", ""],
    )
    return f"""{output}
{nodal_str}
{element_str}
{contact_str}""".strip()
