import re

from .helper_utils import AbaFF

_re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

# Elements
re_el = re.compile(
    r"^\*Element,\s*type=(?P<eltype>.*?)(?:\n|,\s*elset=(?P<elset>.*?)\s*\n)(?<=)(?P<members>(?:.*?)(?=\*|\Z))",
    _re_in,
)

re_sets = re.compile(
    r"(?:\*(nset|elset),\s*(?:nset|elset))=(.*?)(?:,\s*(internal\s*)|(?:))"
    r"(?:,\s*instance=(.*?)|(?:))(?:,\s*(generate)|(?:))\s*\n(?<=)((?:.*?)(?=\*|\Z))",
    _re_in,
)
# Boundary Conditions
re_bcs = re.compile(
    r"(?:\*\*\s*Name:\s*(?P<name>.*?)\s*Type:\s*(?P<type>.*?)\n|)" r"\*Boundary\n(?<=)(?P<content>(?:.*?)(?=\*|\Z))",
    _re_in,
)

# Parts
parts_matches = re.compile(r"\*Part, name=(?P<name>.*?)\n(?P<bulk_str>.*?)\*End Part", _re_in)
part_names = re.compile(r"\*\*\s*PART INSTANCE:\s*(.*?)\n(.*)", _re_in)

# Instances
inst_matches = re.compile(
    r"\*Instance, name=(?P<inst_name>.*?), part=(?P<part_name>.*?)\n(?P<bulk_str>.*?)\*End Instance", _re_in
)

# Sections
_re_offset = r"(?:, offset=(?P<offset>.*?)|)"
_re_controls = r"(?:, controls=(?P<controls>.*?)|)"
re_shell = re.compile(
    r"\*\Shell Section, elset"
    rf"=(?P<elset>.*?)\s*, material=(?P<material>.*?){_re_offset}{_re_controls}\s*\n(?P<t>.*?),"
    rf"(?P<int_points>.*?)$",
    _re_in,
)

re_beam = re.compile(
    r"\*Beam Section,\s*elset=(?P<elset>.*?)\s*,\s*material=(?P<material>.*?)\s*,\s*"
    r"(?:temperature=(?P<temperature>.*?),|)\s*(?:section=|sect=)(?P<sec_type>.*?)\n"
    r"(?P<line1>.*?)\n(?P<line2>.*?)$",
    _re_in,
)

re_solid = re.compile(
    r"(?:\*\s*Section:\s*(.*?)\n|)\*\Solid Section,\s*elset=(.*?)\s*,\s*material=(.*?)\s*$",
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
sh2so_re = AbaFF("Shell to Solid Coupling", [("constraint name=", "influence distance=|"), ("surf1", "surf2")])
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
