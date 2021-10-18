import re

_re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

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
