import re


class AbaFF:
    """
    Abaqus Fortran Flags. A class designed to aid in building regex searched
    for Abaqus flags.

    """

    def __init__(self, flag, args, subflags=None, nameprop=None):
        """

        :param flag: Main flag. *<FLAG>
        :param args: arguments. Tuple of arguments
        :param subflags:
        :param nameprop:


        type of arguments:

        >: up to

        """
        self._flag = flag
        self._subflags = subflags if subflags is not None else []
        self._nameprop = nameprop
        self._args = args

    def _create_regex(self, flag, nameprop, args):
        regstr = ""
        if nameprop is not None:
            regstr += rf"\*\*\s*{nameprop[0]}:\s*(?P<{nameprop[1]}>.*?)\n"
        regstr += rf"\*{flag}"
        for i, arg in enumerate(args):
            for j, fl in enumerate(arg):
                subfl = fl.replace("=", "").replace("|", "").replace(">", "")
                exact = True if "==" in fl else False
                if exact:
                    equality = False
                else:
                    equality = True if "=" in fl else False
                optional = True if "|" in fl else False
                uptostar = True if ">" in fl else False

                last_char = regstr[-10:]
                regstr += "(?:"

                if r"(?:\n|)\s*" != last_char:
                    regstr += ","

                clean_name = subfl.replace(" ", "_").replace(r"\*", "")

                if equality:
                    regstr += rf"\s*{subfl}=(?P<{clean_name}>"
                else:
                    regstr += rf"\s*(?P<{clean_name}>"

                if uptostar:
                    regstr += r"(?:(?!\*).)*"
                else:
                    if exact is True:
                        regstr += subfl
                    else:
                        regstr += ".*?"
                regstr += ")"

                if j + 1 == len(arg):
                    regstr += "$"

                if optional:
                    regstr += "|"
                    if j + 1 == len(arg):
                        regstr += "$"

                regstr += ")"
            regstr += r"(?:\n|)\s*"
        return regstr

    @property
    def regstr(self):
        regstr = self._create_regex(self._flag, self._nameprop, self._args)
        for v in self._subflags:
            clean_name = v[0].replace(" ", "_").replace(r"\*", "")
            regstr += f"(:?(?P<{clean_name}>)"
            regstr += self._create_regex(v[0], None, v[1])
            regstr += ")"
        return regstr

    @property
    def regex(self):
        return re.compile(self.regstr, re.IGNORECASE | re.MULTILINE | re.DOTALL)


class AbaCards:
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
        [("Connector Elasticity", [("nonlinear|", "component=|"), ("bulk>",)])],
    )
    connector_section = AbaFF("Connector Section", [("elset=", "behavior="), ("contype",), ("csys",)])

    # Constraints
    sh2so_re = AbaFF("Shell to Solid Coupling", [("constraint name=",), ("surf1", "surf2")])
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
            ("ax", "ay", "az", "bx", "by", "bz", "|cx", "|cy", "|cz"),
            ("v1", "v2"),
        ],
    )
