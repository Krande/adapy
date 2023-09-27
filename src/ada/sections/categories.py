from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Section


class BaseTypes(Enum):
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

    @staticmethod
    def from_str(type_str: str):
        key_map = {x.value.lower(): x for x in BaseTypes}
        result = key_map.get(type_str.lower(), None)

        if result is None:
            result = SectionCat.get_shape_type(type_str.upper())
            if result is None:
                raise ValueError()

        return result

    @staticmethod
    def get_valid_example_map() -> dict[BaseTypes, str]:
        """Returns a map of valid section types and an example of each type."""
        return {
            BaseTypes.BOX: "BG800x600x20x30",
            BaseTypes.TUBULAR: "TUB375x35",
            BaseTypes.IPROFILE: "HEA300",
            BaseTypes.TPROFILE: "TG650x300x25x40",
            BaseTypes.ANGULAR: "HP180x10",
            BaseTypes.CHANNEL: "UNP180x10",
            BaseTypes.CIRCULAR: "CIRC100",
            BaseTypes.FLATBAR: "FB100x10",
        }


class SectionCat:
    BASETYPES = BaseTypes

    box = [BASETYPES.BOX.value, "BG", "CG"]
    shs = ["SHS"]
    rhs = ["RHS", "URHS"]
    tubular = [BASETYPES.TUBULAR.value, "PIPE", "OD"]
    iprofiles = ["HEA", "HEB", "HEM", "IPE"]
    igirders = [BASETYPES.IPROFILE.value, "IG"]
    tprofiles = [BASETYPES.TPROFILE.value, "TG"]
    angular = [BASETYPES.ANGULAR.value]
    channels = [BASETYPES.CHANNEL.value]
    circular = [BASETYPES.CIRCULAR.value]
    general = [BASETYPES.GENERAL.value, "GENBEAM"]
    flatbar = [BASETYPES.FLATBAR.value]
    poly = ["POLY"]

    @staticmethod
    def _get_sec_type(section_ref: str | Section) -> str | BaseTypes:
        from ada import Section

        if isinstance(section_ref, Section):
            return section_ref.type
        return section_ref.upper()

    @classmethod
    def get_shape_type(cls, bm_type):
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
            if type_func(bm_type):
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
