from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from ada.fem.formats.utils import get_ff_regex
from ada.sections.categories import BaseTypes

re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL


@dataclass
class DataCard:
    name: str
    components: tuple

    def __post_init__(self):
        self._index_map = {x.upper(): i for i, x in enumerate(self.components)}

    def to_ff_re(self):
        return get_ff_regex(self.name, *self.components)

    def get_indices_from_names(self, names: list[str]) -> list[str] | str:
        res = [self._index_map[n.upper()] for n in names]
        return res if len(res) != 1 else res[0]

    def get_data_map_from_names(self, names: list[str], data: list) -> dict:
        indices = self.get_indices_from_names(names)
        output = dict()
        for i, name in zip(indices, names):
            output[name] = data[i]
        return output

    def cast_to_np(self, names, data):
        indices = self.get_indices_from_names(names)
        return np.array([[d[i] for i in indices] for d in data])

    @staticmethod
    def is_numeric(stripped: str):
        if stripped[0].isnumeric() is False and stripped[0] != "-":
            return False
        return True

    @staticmethod
    def str_to_proper_types(split_str: str) -> list[float | str]:
        return [float(x) if DataCard.is_numeric(x) else x for x in split_str.split()]


SEC_MAP = {
    "GIORH": (
        BaseTypes.IPROFILE,
        (("hz", "h"), ("ty", "t_w"), ("bt", "w_top"), ("tt", "t_ftop"), ("bb", "w_btn"), ("tb", "t_fbtn")),
    ),
    "GBOX": (
        BaseTypes.BOX,
        (("hz", "h"), ("ty", "t_w"), ("by", "w_top"), ("tt", "t_ftop"), ("by", "w_btn"), ("tb", "t_fbtn")),
    ),
}
MAT_MAP = {
    "MISOSEL": (
        ("young", "E"),
        ("poiss", "v"),
        ("rho", "rho"),
        ("damp", "zeta"),
        ("alpha", "alpha"),
        ("yield", "sig_y"),
    ),
    "MORSMEL": (("d11", "E"), ("ps1", "v"), ("rho", "rho"), ("alpha1", "alpha"), ("damp1", "zeta")),
}

# Nodes
GNODE = DataCard("GNODE", ("nodex", "nodeno", "ndof", "odof"))
GCOORD = DataCard("GCOORD", ("id", "x", "y", "z"))

# Elements
GELMNT1 = DataCard("GELMNT1", ("elnox", "elno", "eltyp", "eltyad", "nids"))
GELREF1 = DataCard(
    "GELREF1",
    (
        "elno",
        "matno",
        "addno",
        "intno",
        "mintno",
        "strano",
        "streno",
        "strepono",
        "geono",
        "fixno",
        "eccno",
        "transno",
        "members|",
    ),
)

# Beam Sections
# GIORH (I-section)
# GUSYI (unsymm.I-section)
# GCHAN (Channel section)
# GBOX (Box section)
# GPIPE (Pipe section)
# GBARM (Massive bar)
# GTONP (T on plate)
# GDOBO (Double box)
# GLSEC (L section)
# GIORHR
# GCHANR
# GLSECR


TDSECT = DataCard("TDSECT", ("nfield", "geono", "codnam", "codtxt", "set_name"))
re_sectnames = TDSECT.to_ff_re()
re_gbeamg = get_ff_regex(
    "GBEAMG",
    "geono",
    "comp",
    "area",
    "ix",
    "iy",
    "iz",
    "iyz",
    "wxmin",
    "wymin",
    "wzmin",
    "shary",
    "sharz",
    "shceny",
    "shcenz",
    "sy",
    "sz",
    "wy|",
    "wz|",
    "fabr|",
)
GIORH = DataCard("GIORH", ("geono", "hz", "ty", "bt", "tt", "bb", "tb", "sfy", "sfz", "NLOBYT|", "NLOBYB|", "NLOBZ|"))
GBOX = DataCard("GBOX", ("geono", "hz", "ty", "tb", "tt", "by", "sfy", "sfz"))
re_gbox = GBOX.to_ff_re()
re_gpipe = get_ff_regex("GPIPE", "geono", "di", "dy", "t", "sfy", "sfz")
re_gbarm = get_ff_regex("GBARM", "geono", "hz", "bt", "bb", "sfy", "sfz")

# Coordinate System
GUNIVEC = DataCard("GUNIVEC", ("transno", "unix", "uniy", "uniz"))

# Shell section
GELTH = DataCard("GELTH", ("geono", "th", "nint|"))

# Other
re_bnbcd = get_ff_regex("BNBCD", "nodeno", "ndof", "content")
re_belfix = get_ff_regex(
    "BELFIX",
    "fixno",
    "opt",
    "trano",
    "unused",
    "a1|",
    "a2|",
    "a3|",
    "a4|",
    "a5|",
    "a6|",
)
re_mgsprng = get_ff_regex("MGSPRNG", "matno", "ndof", "bulk")
re_bnmass = get_ff_regex("BNMASS", "nodeno", "ndof", "m1", "m2", "m3", "m4", "m5", "m6")
re_mgmass = get_ff_regex("MGMASS", "matno", "ndof", "bulk")
re_geccen = get_ff_regex("GECCEN", "eccno", "ex", "ey", "ez")
re_bldep = get_ff_regex("BLDEP", "slave", "master", "nddof", "ndep", "bulk")

GSETMEMB = DataCard("GSETMEMB", ("nfield", "isref", "index", "istype", "isorig", "members"))
re_setmembs = GSETMEMB.to_ff_re()

TDSETNAM = DataCard("TDSETNAM", ("nfield", "isref", "codnam", "codtxt", "set_name"))
re_setnames = TDSETNAM.to_ff_re()

# Materials

TDMATER = DataCard("TDMATER", ("nfield", "geo_no", "codnam", "codtxt", "name"))
MISOSEL = DataCard("MISOSEL", ("matno", "young", "poiss", "rho", "damp", "alpha", "iyield", "yield"))
MORSMEL = DataCard(
    "MORSMEL",
    (
        "matno",
        "q1",
        "q2",
        "q3",
        "rho",
        "d11",
        "d21",
        "d22",
        "d31",
        "d32",
        "d33",
        "ps1",
        "ps2",
        "damp1",
        "damp2",
        "alpha1",
        "alpha2",
    ),
)

# Results
RDNODRES = DataCard("RDNODRES", ["nfield", "irdva", "lenrec"])
RVNODDIS = DataCard(
    "RVNODDIS", ["nfield", "ires", "inod", "irdva|", "itrans|", "U1|", "U2|", "U3|", "U4|", "U5|", "U6|"]
)
RDPOINTS = DataCard(
    "RDPOINTS", ["nfield", "ispalt", "iielno", "icoref", "ieltyp", "nsp", "ijkdim", "nsptra", "nlay", "bulk"]
)
RDSTRESS = DataCard("RDSTRESS", ["nfield", "irstrs", "lenrec", "bulk"])
RVSTRESS = DataCard("RVSTRESS", ["nfield", "ires", "iielno", "ispalt", "irstrs"])
RDIELCOR = DataCard("RDIELCOR", ["nfield, icoref", "igrid"])
RDRESREF = DataCard("RDRESREF", ["nfield", "ires", "irno", "ieres", "icalty", "complx", "numtyp", "bulk"])
RVFORCES = DataCard("RVFORCES", ["nfield", "ires", "ielno", "ispalt", "irforc|", "bulk|"])
RDFORCES = DataCard("RDFORCES", ["nfield", "irforc", "lenrec", "bulk|"])

re_rvforces = RVFORCES.to_ff_re()
re_rdforces = RDFORCES.to_ff_re()

re_rsumreac = get_ff_regex("RSUMREAC", "nfield", "ires", "ircomp", "x", "y", "z", "rx", "ry", "rz")
re_rvnodrea = get_ff_regex(
    "RVNODREA", "nfield", "ires", "inod", "irrea|", "irboc|", "itrans|", "F1|", "F2|", "F3|", "F4|", "F5|", "F6|"
)

re_rdrescmb = get_ff_regex("RDRESCMB", "nfield", "ires", "complx", "nres", "bulk")

TDRESREF = DataCard("TDRESREF", ("nfield", "ires", "codnam", "codtxt", "name"))
re_tdresref = TDRESREF.to_ff_re()
re_tdload = get_ff_regex("TDLOAD", "nfield", "llc", "codnam", "codtxt", "name")
