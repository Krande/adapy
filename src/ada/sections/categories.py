from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Section


class BaseTypes:
    BOX = "BOX"
    TUBULAR = "TUB"
    IPROFILE = "I"
    TPROFILE = "T"
    ANGULAR = "HP"
    CHANNEL = "UNP"
    CIRCULAR = "CIRC"
    GENERAL = "GENERAL"
    FLATBAR = "FB"
    POLY = "poly"


class SectionCat:

    BASETYPES = BaseTypes

    box = [BASETYPES.BOX, "BG", "CG"]
    shs = ["SHS"]
    rhs = ["RHS", "URHS"]
    tubular = [BASETYPES.TUBULAR, "PIPE", "OD"]
    iprofiles = ["HEA", "HEB", "HEM", "IPE"]
    igirders = [BASETYPES.IPROFILE, "IG"]
    tprofiles = [BASETYPES.TPROFILE, "TG"]
    angular = [BASETYPES.ANGULAR]
    channels = [BASETYPES.CHANNEL]
    circular = [BASETYPES.CIRCULAR]
    general = [BASETYPES.GENERAL, "GENBEAM"]
    flatbar = [BASETYPES.FLATBAR]
    poly = ["POLY"]

    @classmethod
    def isbeam(cls, bmtype):
        for key, val in cls.__dict__.items():
            if bmtype in val:
                return True
        return False

    @staticmethod
    def _get_sec_type(section_ref):
        from ada import Beam, Section

        if type(section_ref) is Section:
            return section_ref.type.upper()
        if type(section_ref) is Beam:
            return section_ref.section.type.upper()
        else:
            return section_ref.upper()

    @classmethod
    def get_shape_type(cls, bmtype):
        type_map = [
            (cls.is_i_profile, cls.BASETYPES.IPROFILE),
            (cls.is_angular, cls.BASETYPES.ANGULAR),
            (cls.is_flatbar, cls.BASETYPES.FLATBAR),
            (cls.is_box_profile, cls.BASETYPES.BOX),
            (cls.is_t_profile, cls.BASETYPES.TPROFILE),
            (cls.is_channel_profile, cls.BASETYPES.CHANNEL),
            (cls.is_tubular_profile, cls.BASETYPES.TUBULAR),
            (cls.is_circular_profile, cls.BASETYPES.CIRCULAR),
            (cls.is_general, cls.BASETYPES.GENERAL),
            (cls.is_poly, cls.BASETYPES.POLY),
        ]

        for type_func, return_type in type_map:
            if type_func(bmtype):
                return return_type

    @classmethod
    def is_i_profile(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.igirders + cls.iprofiles

    @classmethod
    def is_t_profile(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.tprofiles

    @classmethod
    def is_box_profile(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.box + cls.shs + cls.rhs

    @classmethod
    def is_circular_profile(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.circular

    @classmethod
    def is_tubular_profile(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.tubular

    @classmethod
    def is_channel_profile(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.channels

    @classmethod
    def is_flatbar(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.flatbar

    @classmethod
    def is_general(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.general

    @classmethod
    def is_angular(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.angular

    @classmethod
    def is_poly(cls, bmtype) -> bool:
        return cls._get_sec_type(bmtype) in cls.poly

    @classmethod
    def is_strong_axis_symmetric(cls, section: Section) -> bool:
        return section.w_top == section.w_btn and section.t_ftop == section.t_fbtn
