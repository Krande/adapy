import re


class AbaFF:
    """"""

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
        from ada.fem.io import FemObjectReader

        return re.compile(self.regstr, FemObjectReader.re_in)
