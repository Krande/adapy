from __future__ import annotations

import re
from typing import TYPE_CHECKING, Union

_re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

if TYPE_CHECKING:
    from ada import FEM, Part
    from ada.fem import FemSet, Surface


class AbaFF:
    """Abaqus Fortran Flags. A class designed to aid in building regex searched for Abaqus flags."""

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


def list_cleanup(membulkstr):
    return membulkstr.replace(",\n", ",").replace("\n", ",")


def is_set_in_part(part: Part, set_name: str, set_type) -> Union[FemSet, Surface]:
    set_map = {"nset": part.fem.nsets, "elset": part.fem.elsets, "surface": part.fem.surfaces}
    id_map = {"nset": part.fem.nodes, "elset": part.fem.elements}

    if str.isnumeric(set_name):
        _id = int(set_name)
        return id_map[set_type].from_id(_id)

    if set_name in set_map[set_type].keys():
        return set_map[set_type][set_name]

    raise ValueError()


def get_set_from_assembly(set_str: str, fem: "FEM", set_type) -> Union["FemSet", "Surface"]:
    res = set_str.split(".")

    if len(res) == 1:
        local_set_map = {"nset": fem.nsets, "elset": fem.elsets, "surface": fem.surfaces}
        set_name = res[0]
        return local_set_map[set_type][set_name]

    set_name = res[1]
    p_name = res[0]

    if str.isnumeric(set_name):
        num_id = int(set_name)
        local_id_map = {"nset": fem.nodes.from_id, "elset": fem.elements.from_id}
        if p_name == fem.name:
            return local_id_map[set_type](num_id)
        for part in fem.parent.get_all_parts_in_assembly():
            if p_name == part.fem.instance_name:
                r = is_set_in_part(part, set_name, set_type)
                if r is not None:
                    return r
    else:
        local_set_map = {"nset": fem.nsets, "elset": fem.elsets, "surface": fem.surfaces}

        if p_name == fem.name:
            return local_set_map[p_name]
        for part in fem.parent.get_all_parts_in_assembly():
            if p_name == part.fem.instance_name:
                r = is_set_in_part(part, set_name, set_type)
                if r is not None:
                    return r
    raise ValueError(f'No {set_type} "{set_str}" was found')
