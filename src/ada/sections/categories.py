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


class SectionCat:
    box = ["BG", "CG"]
    shs = ["SHS"]
    rhs = ["RHS", "URHS"]
    tubular = ["TUB", "PIPE", "OD"]
    iprofiles = ["HEA", "HEB", "HEM", "IPE"]
    igirders = ["IG"]
    tprofiles = ["TG"]
    angular = ["HP"]
    channels = ["UNP"]
    circular = ["CIRC"]
    general = ["GENBEAM"]
    flatbar = ["FB"]

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
    def is_i_profile(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.igirders + cls.iprofiles else False

    @classmethod
    def is_t_profile(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.tprofiles else False

    @classmethod
    def is_box_profile(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.box + cls.shs + cls.rhs else False

    @classmethod
    def is_hp_profile(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.angular else False

    @classmethod
    def is_circular_profile(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.circular else False

    @classmethod
    def is_tubular_profile(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.tubular else False

    @classmethod
    def is_channel_profile(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.channels else False

    @classmethod
    def is_flatbar(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.flatbar else False

    @classmethod
    def is_general(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.general else False

    @classmethod
    def is_angular(cls, bmtype):
        return True if cls._get_sec_type(bmtype) in cls.angular else False

    @classmethod
    def is_strong_axis_symmetric(cls, section):
        """

        :param section:
        :type section: ada.Section
        :return:
        """
        return section.w_top == section.w_btn and section.t_ftop == section.t_fbtn
