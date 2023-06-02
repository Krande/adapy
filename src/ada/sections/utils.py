from __future__ import annotations

import json
import pathlib
import re
from collections import OrderedDict

import ada.core.utils
from ada.base.units import Units
from ada.core.exceptions import UnsupportedUnits

from . import Section
from .categories import SectionCat

digit = r"\d{0,5}\.?\d{0,5}|\d{0,5}|\d{0,5}\/\d{0,5}"
flex = r"?:\.|[A-Z]|"


class UnableToConvertSectionError(Exception):
    pass


def profile_db_collect(sec_type: str, dim: str, units: Units = Units.M):
    """Return a section object based on values in a profile db json document. Source JSON is in units 'm' meters."""

    scale_factor = Units.get_scale_factor(units, Units.M)

    if scale_factor is None:
        raise UnsupportedUnits(f'Units "{units}" is not supported')

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

    if sec_type not in profile_db.keys():
        return None

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


_re_in = re.IGNORECASE | re.DOTALL
_rdoff = ada.core.utils.roundoff


def interpret_section_str(in_str: str, s=0.001, units=Units.M) -> tuple[Section, Section]:
    """

    :param in_str:
    :param s: Scale factor
    :param units: The desired units after applied scale factor
    :return: Two section (to account for potential beam tapering)
    """

    for section_eval in [box_section, shs_section, rhs_section, ig_section, tg_section]:
        result = section_eval(in_str, s, units)
        if result is not None:
            return result

    if "pipe" not in in_str.lower():
        result = iprofile_section(in_str, s, units)
        if result is not None:
            return result

    for section_eval in [tub_section, angular_section, circ_section, channel_section, flat_section]:
        result = section_eval(in_str, s, units)
        if result is not None:
            return result

    raise UnableToConvertSectionError(f'Unable to interpret section str "{in_str}"')


def get_section(sec: Section | str) -> tuple[Section, Section]:
    if isinstance(sec, Section):
        return sec, sec
    elif isinstance(sec, str):
        return interpret_section_str(sec)
    else:
        raise ValueError("Unable to find beam section based on input: {}".format(sec))


def box_section(in_str: str, s: float, units: Units):
    for box in SectionCat.box:
        res = re.search(
            r"({box})({flex})({digit})x({digit})x({digit})x({digit})".format(box=box, flex=flex, digit=digit),
            in_str,
            _re_in,
        )
        if res is None:
            continue
        h = [_rdoff(float(x) * s) for x in res.group(2).split("/")]
        width = [_rdoff(float(x) * s) for x in res.group(3).split("/")]
        tw = [_rdoff(float(x) * s) for x in res.group(4).split("/")]
        tf = [_rdoff(float(x) * s) for x in res.group(5).split("/")]
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
        if "/" in in_str:
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
        else:
            tap = sec
        return sec, tap


def shs_section(in_str: str, s: float, units: Units):
    for shs in SectionCat.shs:
        res = re.search(
            r"({hol})({flex})({digit})x({digit})".format(hol=shs, flex=flex, digit=digit),
            in_str,
            _re_in,
        )

        if res is None:
            continue

        h = [_rdoff(float(x) * s) for x in res.group(2).split("/")]
        width = [_rdoff(float(x) * s) for x in res.group(3).split("/")]
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
        if "/" in in_str:
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
        else:
            tap = sec
        return sec, tap


def rhs_section(in_str: str, s: float, units: Units):
    for rhs in SectionCat.rhs:
        res = re.search(
            r"({rhs})({flex})({digit})x({digit})x({digit})".format(rhs=rhs, flex=flex, digit=digit),
            in_str,
            _re_in,
        )

        if res is None:
            continue
        h = [_rdoff(float(x) * s) for x in res.group(2).split("/")]
        width = [_rdoff(float(x) * s) for x in res.group(3).split("/")]
        tw = [_rdoff(float(x) * s) for x in res.group(4).split("/")]
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


def ig_section(in_str: str, s: float, units: Units):
    for ig in SectionCat.igirders:
        res = re.search(
            "({ig})({flex})({digit})x({digit})x({digit})x({digit})".format(ig=ig, flex=flex, digit=digit),
            in_str,
            _re_in,
        )
        if res is None:
            continue
        h = [_rdoff(float(x) * s) for x in res.group(2).split("/")]
        wt = [_rdoff(float(x) * s) for x in res.group(3).split("/")]
        tw = [_rdoff(float(x) * s) for x in res.group(4).split("/")]
        tf = [_rdoff(float(x) * s) for x in res.group(5).split("/")]
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


def tg_section(in_str: str, s: float, units: Units):
    for tg in SectionCat.tprofiles:
        res = re.search(
            "({ig})({flex})({digit})x({digit})x({digit})x({digit})".format(ig=tg, flex=flex, digit=digit),
            in_str,
            _re_in,
        )
        if res is None:
            continue
        h = [_rdoff(float(x) * s) for x in res.group(2).split("/")]
        wt = [_rdoff(float(x) * s) for x in res.group(3).split("/")]
        tw = [_rdoff(float(x) * s) for x in res.group(4).split("/")]
        tf = [_rdoff(float(x) * s) for x in res.group(5).split("/")]
        sec = Section(
            in_str,
            h=h[0],
            sec_type=SectionCat.BASETYPES.TPROFILE,
            w_btn=tw[0],
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
            sec_type=SectionCat.BASETYPES.TPROFILE,
            w_btn=wt[-1],
            w_top=tw[-1],
            t_fbtn=tf[-1],
            t_ftop=tf[-1],
            t_w=tw[-1],
            metadata=dict(cad_str=in_str),
            units=units,
        )
        return sec, tap


def iprofile_section(in_str: str, s: float, units: Units):
    for ipe in SectionCat.iprofiles:
        res = re.search("({ipe})({digit})".format(ipe=ipe, digit=digit), in_str, _re_in)
        if res is not None:
            sec = profile_db_collect(ipe, res.group(2), units=units)
            return sec, sec
        else:
            if "HE" not in ipe:
                continue
            shuffle = "({ipe})({digit}){end}".format(ipe=ipe[:2], digit=digit, end=ipe[2:])
            res = re.search(shuffle, in_str, _re_in)
            if res is not None:
                sec = profile_db_collect(ipe, res.group(2), units=units)
                return sec, sec


def tub_section(in_str: str, s: float, units: Units):
    for tub in SectionCat.tubular:
        res = re.search("({tub})({digit})x({digit})".format(tub=tub, digit=digit), in_str, _re_in)
        if res is None:
            continue
        fac = 0.5 if tub in ["OD", "O"] else 1.0
        r = [_rdoff(float(x) * s * fac) for x in res.group(2).split("/")]
        wt = [_rdoff(float(x) * s) for x in res.group(3).split("/")]
        sec = Section(
            in_str,
            sec_type=SectionCat.BASETYPES.TUBULAR,
            r=r[0],
            wt=wt[0],
            metadata=dict(cad_str=in_str),
            units=units,
        )
        if len(r) == 1:
            return sec, sec

        tap = Section(
            in_str + "_e",
            sec_type=SectionCat.BASETYPES.TUBULAR,
            r=r[-1],
            wt=wt[-1],
            metadata=dict(cad_str=in_str),
            units=units,
        )
        return sec, tap


def angular_section(in_str: str, s: float, units: Units):
    for ang in SectionCat.angular:
        res = re.search("({ang})({digit})x({digit})".format(ang=ang, digit=digit), in_str, _re_in)
        if res is None:
            continue
        sec = profile_db_collect(ang, "{}x{}".format(res.group(2), res.group(3)), units=units)
        return sec, sec


def circ_section(in_str: str, s: float, units: Units):
    for circ in SectionCat.circular:
        res = re.search("{circ}(.*?)$".format(circ=circ), in_str, _re_in)
        if res is None:
            continue
        sec = Section(
            in_str,
            sec_type=SectionCat.BASETYPES.CIRCULAR,
            r=_rdoff(float(res.group(1)) * s),
            metadata=dict(cad_str=in_str),
            units=units,
        )
        return sec, sec


def channel_section(in_str: str, s: float, units: Units):
    for cha in SectionCat.channels:
        res = re.search("({ang})({digit})".format(ang=cha, digit=digit), in_str, _re_in)
        if res is None:
            continue
        sec = profile_db_collect(cha, res.group(2), units=units)
        return sec, sec


def flat_section(in_str: str, s: float, units: Units):
    for flat in SectionCat.flatbar:
        res = re.search("({flat})({digit})x({digit})".format(flat=flat, digit=digit), in_str, _re_in)
        if res is None:
            continue
        h = [_rdoff(float(x) * s) for x in res.group(2).split("/")]
        width = [_rdoff(float(x) * s) for x in res.group(3).split("/")]

        sec = Section(
            in_str,
            sec_type=SectionCat.BASETYPES.FLATBAR,
            h=h[0],
            w_top=width[0],
            w_btn=width[0],
            metadata=dict(cad_str=in_str),
            units=units,
        )
        if "/" in in_str:
            tap = Section(
                in_str + "_e",
                sec_type=SectionCat.BASETYPES.FLATBAR,
                h=h[-1],
                w_top=width[-1],
                w_btn=width[-1],
                metadata=dict(cad_str=in_str),
                units=units,
            )
        else:
            tap = sec
        return sec, tap
