import re

from .helper_utils import AbaFF

_re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

# Every keyword value and every data value in an Abaqus deck lives on a single line, so those
# groups are spelled ``[^\n]*?`` and not ``.*?``. Under the DOTALL above, ``.*?`` matches newlines
# too, and "non-greedy" only means "expand as little as possible *until the rest of the pattern
# matches*" -- so a group whose terminator is missing on its own line keeps expanding until the
# terminator turns up, which can be the far end of the file. That is how a ``** Section:`` comment
# on a shell section came to capture 1.18M lines as a solid section's name. Groups that are
# genuinely multi-line (element/set member blocks, part and instance bodies) stay ``.*?``, bounded
# by a lookahead for the next keyword instead.

# Elements
re_el = re.compile(
    r"^\*Element,\s*type=(?P<eltype>[^\n]*?)(?:\n|,\s*elset=(?P<elset>[^\n]*?)\s*\n)"
    r"(?<=)(?P<members>(?:.*?)(?=\*|\Z))",
    _re_in,
)

re_sets = re.compile(
    r"(?:\*(nset|elset),\s*(?:nset|elset))=([^\n]*?)(?:,\s*(internal\s*)|(?:))"
    r"(?:,\s*instance=([^\n]*?)|(?:))(?:,\s*(generate)|(?:))\s*\n(?<=)((?:.*?)(?=\*|\Z))",
    _re_in,
)
# Boundary Conditions
re_bcs = re.compile(
    r"(?:\*\*\s*Name:\s*(?P<name>[^\n]*?)\s*Type:\s*(?P<type>[^\n]*?)\n|)"
    r"\*Boundary\n(?<=)(?P<content>(?:.*?)(?=\*|\Z))",
    _re_in,
)

# Parts
parts_matches = re.compile(r"\*Part, name=(?P<name>[^\n]*?)\n(?P<bulk_str>.*?)\*End Part", _re_in)
part_names = re.compile(r"\*\*\s*PART INSTANCE:\s*([^\n]*?)\n(.*)", _re_in)

# Instances
inst_matches = re.compile(
    r"\*Instance, name=(?P<inst_name>[^\n]*?), part=(?P<part_name>[^\n]*?)\n(?P<bulk_str>.*?)\*End Instance", _re_in
)

# Sections
_re_offset = r"(?:, offset=(?P<offset>[^\n]*?)|)"
_re_controls = r"(?:, controls=(?P<controls>[^\n]*?)|)"
re_shell = re.compile(
    r"\*Shell Section, elset"
    rf"=(?P<elset>[^\n]*?)\s*, material=(?P<material>[^\n]*?){_re_offset}{_re_controls}\s*\n(?P<t>[^\n]*?),"
    rf"(?P<int_points>[^\n]*?)$",
    _re_in,
)

re_beam = re.compile(
    r"\*Beam Section,\s*elset=(?P<elset>[^\n]*?)\s*,\s*material=(?P<material>[^\n]*?)\s*,\s*"
    r"(?:temperature=(?P<temperature>[^\n]*?),|)\s*(?:section=|sect=)(?P<sec_type>[^\n]*?)\n"
    r"(?P<line1>[^\n]*?)\n(?P<line2>[^\n]*?)$",
    _re_in,
)

re_solid = re.compile(
    r"(?:\*\s*Section:\s*(?P<name>[^\n]*?)\n|)"
    r"\*Solid Section,\s*elset=(?P<elset>[^\n]*?)\s*,\s*material=(?P<material>[^\n]*?)\s*$",
    _re_in,
)

# Contact
contact_pairs = AbaFF(
    "Contact Pair",
    [
        (
            "interaction=",
            "small sliding==|",
            "type=|",
            "adjust=|",
            "mechanical constraint=|",
            "geometric correction=|",
            "cpset=|",
        ),
        ("surf1", "surf2"),
    ],
    nameprop=("Interaction", "name"),
)

contact_general = AbaFF(
    "Contact",
    args=[()],
    subflags=[
        ("Contact Inclusions", [(), ("surf1", "surf2")]),
        ("Contact Property Assignment", [(), ("vara", "varb", "interaction")]),
        ("Contact Formulation", [("type=",), ("csurf1", "csurf2", "csurf_type")]),
        ("Contact Initialization Assignment", [(), ("ssurf1", "ssurf2", "cinit")]),
        ("Surface Property Assignment", [("property=",), ("bulk>",)]),
    ],
    # nameprop=("Interaction", "name"),
)

# Connectors
connector_behaviour = AbaFF(
    "Connector Behavior",
    [("name=",)],
    [("Connector Elasticity", [("nonlinear|", "component=|", "dependencies=|"), ("bulk>",)])],
)
connector_section = AbaFF("Connector Section", [("elset=", "behavior="), ("contype",), ("csys",)])

# Constraints
sh2so_re = AbaFF(
    "Shell to Solid Coupling",
    # Abaqus sizes the coupling region with either "influence distance" or
    # "position tolerance". Without the latter listed the name group ran to the end
    # of the line and swallowed it.
    [("constraint name=", "influence distance=|", "position tolerance=|"), ("surf1", "surf2")],
)
rigid_bodies = AbaFF("Rigid Body", [("ref node=", "elset=")])
coupling = AbaFF(
    "Coupling",
    [("constraint name=", "ref node=", "surface=", "orientation=|")],
    [("Kinematic", [(), ("bulk>",)])],
)
tie = AbaFF("Tie", [("name=", "adjust="), ("surf1", "surf2")])
# Other
surface_smoothing = AbaFF("Surface Smoothing", [("name=",), ("bulk>",)])
surface = AbaFF("Surface", [("type=", "name=", "internal|"), ("bulk>",)])
orientation = AbaFF(
    "Orientation",
    [
        ("name=", "definition=|", "local directions=|", "system=|"),
        ("ax", "ay", "az", "bx|", "by|", "bz|", "|cx", "|cy", "|cz"),
        ("v1|", "v2|"),
    ],
)
