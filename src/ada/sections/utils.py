import json
import pathlib
import re
from collections import OrderedDict

import ada.core.utils

from . import SectionCat


def profile_db_collect(sec_type, dim, units="m"):
    """
    Tale in section type and dimensions and return a section object based on values in a
    profile db xml document. The elements in the xml doc is in units meters.

    :param sec_type:
    :param dim:
    :param units:
    :type sec_type: str
    :type dim: str
    :type units: str
    """
    from ada import Section

    if units == "mm":
        scale_factor = 1000
    elif units == "m":
        scale_factor = 1.0
    else:
        raise ValueError(f'Units "{units}" is not supported')

    if sec_type == "IP":
        sec_type = "IPE"
    dir_path = pathlib.Path(__file__).resolve().parent

    with open(dir_path / "resources" / "ProfileDB.json") as data_file:
        profile_db_main = json.load(data_file, object_pairs_hook=OrderedDict)

    profile_db = profile_db_main["ProfileDB"]
    sec_dim = [int(x) for x in dim.split("x")]
    sec_name_alt1 = sec_type + str(dim)
    if "x" in str(dim):
        sec_name_alt2 = sec_type + str(sec_dim[0]) + "x" + str(sec_dim[1])
    else:
        sec_name_alt2 = "Unknown"

    if sec_type in profile_db.keys():
        if sec_name_alt1 in profile_db[sec_type]:
            sec_name = sec_name_alt1
        elif sec_name_alt2 in profile_db[sec_type]:
            sec_name = sec_name_alt2
        else:
            return None
        h = float(profile_db[sec_type][sec_name]["Height"]) * scale_factor
        w_top = float(profile_db[sec_type][sec_name]["Width"]) * scale_factor
        w_btn = float(profile_db[sec_type][sec_name]["Width"]) * scale_factor
        t_w = float(profile_db[sec_type][sec_name]["t_w"]) * scale_factor
        t_fbtn = float(profile_db[sec_type][sec_name]["t_f"]) * scale_factor
        t_ftop = float(profile_db[sec_type][sec_name]["t_f"]) * scale_factor
        return Section(
            sec_name,
            sec_type=sec_type,
            h=h,
            w_top=w_top,
            w_btn=w_btn,
            t_w=t_w,
            t_fbtn=t_fbtn,
            t_ftop=t_ftop,
            sec_str=sec_name,
            metadata=dict(cad_str=sec_name),
            units=units,
        )
    else:
        return None


def interpret_section_str(in_str, s=0.001, units="m"):
    """

    :param in_str:
    :param s: Scale factor
    :param units: The desired units after applied scale factor
    :return: Two section (to account for potential beam tapering)
    """
    from ada import Section

    rdoff = ada.core.utils.roundoff

    digit = r"\d{0,5}\.?\d{0,5}|\d{0,5}|\d{0,5}\/\d{0,5}"
    flex = r"?:\.|[A-Z]|"
    re_in = re.IGNORECASE | re.DOTALL
    for box in SectionCat.box:
        res = re.search(
            r"({box})({flex})({digit})x({digit})x({digit})x({digit})".format(box=box, flex=flex, digit=digit),
            in_str,
            re_in,
        )

        if res is not None:
            h = [rdoff(float(x) * s) for x in res.group(2).split("/")]
            width = [rdoff(float(x) * s) for x in res.group(3).split("/")]
            tw = [rdoff(float(x) * s) for x in res.group(4).split("/")]
            tf = [rdoff(float(x) * s) for x in res.group(5).split("/")]
            sec = Section(
                in_str,
                h=h[0],
                sec_type=box,
                w_btn=width[0],
                w_top=width[0],
                t_fbtn=tf[0],
                t_ftop=tf[0],
                t_w=tw[0],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            tap = Section(
                in_str + "_e",
                h=h[-1],
                sec_type=box,
                w_btn=width[-1],
                w_top=width[-1],
                t_fbtn=tf[-1],
                t_ftop=tf[-1],
                t_w=tw[-1],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            return sec, tap

    for shs in SectionCat.shs:
        res = re.search(
            r"({hol})({flex})({digit})x({digit})".format(hol=shs, flex=flex, digit=digit),
            in_str,
            re_in,
        )

        if res is not None:
            h = [rdoff(float(x) * s) for x in res.group(2).split("/")]
            width = [rdoff(float(x) * s) for x in res.group(3).split("/")]
            sec = Section(
                in_str,
                h=h[0],
                sec_type=shs,
                w_btn=h[0],
                w_top=h[0],
                t_fbtn=width[0],
                t_ftop=width[0],
                t_w=width[0],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            tap = Section(
                in_str + "_e",
                h=h[-1],
                sec_type=shs,
                w_btn=h[-1],
                w_top=h[-1],
                t_fbtn=width[-1],
                t_ftop=width[-1],
                t_w=width[-1],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            return sec, tap

    for rhs in SectionCat.rhs:
        res = re.search(
            r"({rhs})({flex})({digit})x({digit})x({digit})".format(rhs=rhs, flex=flex, digit=digit),
            in_str,
            re_in,
        )

        if res is not None:
            h = [rdoff(float(x) * s) for x in res.group(2).split("/")]
            width = [rdoff(float(x) * s) for x in res.group(3).split("/")]
            tw = [rdoff(float(x) * s) for x in res.group(4).split("/")]
            sec = Section(
                in_str,
                h=h[0],
                sec_type=rhs,
                w_btn=width[0],
                w_top=width[0],
                t_fbtn=tw[0],
                t_ftop=tw[0],
                t_w=tw[0],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            tap = Section(
                in_str + "_e",
                h=h[-1],
                sec_type=rhs,
                w_btn=width[-1],
                w_top=width[-1],
                t_fbtn=tw[-1],
                t_ftop=tw[-1],
                t_w=tw[-1],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            return sec, tap

    for ig in SectionCat.igirders:
        res = re.search(
            "({ig})({flex})({digit})x({digit})x({digit})x({digit})".format(ig=ig, flex=flex, digit=digit),
            in_str,
            re_in,
        )
        if res is not None:
            h = [rdoff(float(x) * s) for x in res.group(2).split("/")]
            wt = [rdoff(float(x) * s) for x in res.group(3).split("/")]
            tw = [rdoff(float(x) * s) for x in res.group(4).split("/")]
            tf = [rdoff(float(x) * s) for x in res.group(5).split("/")]
            sec = Section(
                in_str,
                h=h[0],
                sec_type=ig,
                w_btn=wt[0],
                w_top=wt[0],
                t_fbtn=tf[0],
                t_ftop=tf[0],
                t_w=tw[0],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            tap = Section(
                in_str + "_e",
                h=h[-1],
                sec_type=ig,
                w_btn=wt[-1],
                w_top=wt[-1],
                t_fbtn=tf[-1],
                t_ftop=tf[-1],
                t_w=tw[-1],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            return sec, tap

    if "pipe" not in in_str.lower():
        for ipe in SectionCat.iprofiles:
            res = re.search("({ipe})({digit})".format(ipe=ipe, digit=digit), in_str, re_in)
            if res is not None:
                sec = profile_db_collect(ipe, res.group(2), units=units)
                return sec, sec

    for tub in SectionCat.tubular:
        res = re.search("({tub})({digit})x({digit})".format(tub=tub, digit=digit), in_str, re_in)
        if res is not None:
            fac = 0.5 if tub == "OD" else 1.0
            r = [rdoff(float(x) * s * fac) for x in res.group(2).split("/")]
            wt = [rdoff(float(x) * s) for x in res.group(3).split("/")]
            sec = Section(
                in_str,
                sec_type=tub,
                r=r[0],
                wt=wt[0],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            tap = Section(
                in_str + "_e",
                sec_type=tub,
                r=r[-1],
                wt=wt[-1],
                metadata=dict(cad_str=in_str),
                units=units,
            )
            return sec, tap

    for ang in SectionCat.angular:
        res = re.search("({ang})({digit})x({digit})".format(ang=ang, digit=digit), in_str, re_in)
        if res is not None:
            sec = profile_db_collect(ang, "{}x{}".format(res.group(2), res.group(3)), units=units)
            return sec, sec

    for circ in SectionCat.circular:
        res = re.search("{circ}(.*?)$".format(circ=circ), in_str, re_in)
        if res is not None:
            sec = Section(
                in_str,
                sec_type=circ,
                r=rdoff(float(res.group(1)) * s),
                metadata=dict(cad_str=in_str),
                units=units,
            )
            return sec, sec

    raise ValueError(f'Unable to interpret section str "{in_str}"')
