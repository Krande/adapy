import re

from ada.fem.io.utils import get_ff_regex

re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

# Nodes
re_gnode_in = get_ff_regex("GNODE", "nodex", "nodeno", "ndof", "odof")
re_gcoord_in = get_ff_regex("GCOORD", "id", "x", "y", "z")

# Elements
re_gelmnt = get_ff_regex("GELMNT1", "elnox", "elno", "eltyp", "eltyad", "nids")
re_gelref1 = get_ff_regex(
    "GELREF1",
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
)

# Beam Sections
re_sectnames = get_ff_regex("TDSECT", "nfield", "geono", "codnam", "codtxt", "set_name")
re_giorh = get_ff_regex(
    "GIORH ",
    "geono",
    "hz",
    "ty",
    "bt",
    "tt",
    "bb",
    "tb",
    "sfy",
    "sfz",
    "NLOBYT|",
    "NLOBYB|",
    "NLOBZ|",
)
re_gbox = get_ff_regex("GBOX", "geono", "hz", "ty", "tb", "tt", "by", "sfy", "sfz")
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
re_gpipe = get_ff_regex("GPIPE", "geono", "di", "dy", "t", "sfy", "sfz")
re_lcsys = get_ff_regex("GUNIVEC", "transno", "unix", "uniy", "uniz")

# Shell section
re_thick = get_ff_regex("GELTH", "geono", "th")

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
re_geccen = get_ff_regex("GECCEN", "eccno", "ex", "ey", "ez")
re_bldep = get_ff_regex("BLDEP", "slave", "master", "nddof", "ndep", "bulk")
re_setmembs = get_ff_regex("GSETMEMB", "nfield", "isref", "index", "istype", "isorig", "members")
re_setnames = get_ff_regex("TDSETNAM", "nfield", "isref", "codnam", "codtxt", "set_name")

# Materials
re_matnames = get_ff_regex("TDMATER", "nfield", "geo_no", "codnam", "codtxt", "name")
re_misosel = get_ff_regex("MISOSEL", "matno", "young", "poiss", "rho", "damp", "alpha", "iyield", "yield")
re_morsmel = get_ff_regex(
    "MORSMEL",
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
)

# Results
re_rsumreac = get_ff_regex("RSUMREAC", "nfield", "ires", "ircomp", "x", "y", "z", "rx", "ry", "rz")
re_rvnodrea = get_ff_regex(
    "RVNODREA", "nfield", "ires", "inod", "irrea|", "irboc|", "itrans|", "F1|", "F2|", "F3|", "F4|", "F5|", "F6|"
)
re_rvnoddis = get_ff_regex(
    "RVNODDIS", "nfield", "ires", "inod", "irdva|", "itrans|", "U1|", "U2|", "U3|", "U4|", "U5|", "U6|"
)
re_rdrescmb = get_ff_regex("RDRESCMB", "nfield", "ires", "complx", "nres", "bulk")
re_rvforces = get_ff_regex("RVFORCES", "nfield", "ires", "ielno", "ispalt", "irforc|", "bulk|")
re_rdforces = get_ff_regex("RDFORCES", "nfield", "irforc", "lenrec", "bulk|")
re_tdresref = get_ff_regex("TDRESREF", "nfield", "ires", "codnam", "codtxt", "name")
re_tdload = get_ff_regex("TDLOAD", "nfield", "llc", "codnam", "codtxt", "name")
