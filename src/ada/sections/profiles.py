from ada.core.utils import roundoff as rd


class ProfileBuilder:
    origin = (0.0, 0.0, 0.0)
    """
    A class for creating generalized 2d point curves describing beam section profiles
    """

    @classmethod
    def angular(cls, sec, return_solid):
        h = sec.h
        wbtn = sec.w_btn
        p2 = (0.0, -h)
        p3 = (wbtn, -h)
        disconnected = False
        if return_solid is False:
            disconnected = True
            outer_curve, inner_curve = [(cls.origin[:-1], p2), (p2, p3)], None
        else:
            tf = sec.t_fbtn
            tw = sec.t_w
            p4 = (wbtn, -h + tf)
            p5 = (tw, -h + tf)
            p6 = (tw, 0.0)
            outer_curve, inner_curve = [cls.origin[:-1], p2, p3, p4, p5, p6], None
        return outer_curve, inner_curve, disconnected

    @classmethod
    def iprofiles(cls, sec, return_solid):
        """

        :param sec:
        :param return_solid:
        :type sec: ada.Section
        :type return_solid:
        """
        h = sec.h
        wbtn = sec.w_btn
        wtop = sec.w_top

        # top flange
        c1 = (-wtop / 2, h / 2)
        c2 = (wtop / 2, h / 2)
        # web
        p3 = (0.0, h / 2)
        p4 = (0.0, -h / 2)
        # bottom flange
        c3 = (-wbtn / 2, -h / 2)
        c4 = (wbtn / 2, -h / 2)

        if return_solid is False:
            outer_curve, inner_curve, disconnected = (
                [(c1, c2), (p3, p4), (c3, c4)],
                None,
                True,
            )
        else:
            tfbtn = sec.t_fbtn
            tftop = sec.t_ftop
            tw = sec.t_w
            p3 = (wtop / 2, h / 2 - tftop)
            p4 = (tw / 2, h / 2 - tftop)
            p5 = (tw / 2, -h / 2 + tfbtn)
            p6 = (wbtn / 2, -h / 2 + tfbtn)
            p7 = (-wbtn / 2, -h / 2 + tfbtn)
            p8 = (-tw / 2, -h / 2 + tfbtn)
            p9 = (-tw / 2, h / 2 - tftop)
            p10 = (-wtop / 2, h / 2 - tftop)
            outer_curve, inner_curve, disconnected = (
                [c1, c2, p3, p4, p5, p6, c4, c3, p7, p8, p9, p10],
                None,
                False,
            )

        return outer_curve, inner_curve, disconnected

    @classmethod
    def box(cls, sec, return_solid):
        """

        :param sec:
        :param return_solid:
        :type sec: ada.Section
        :type return_solid:
        """
        h = sec.h
        wtop = sec.w_top
        wbtn = sec.w_btn

        p1 = (rd(-wtop / 2), rd(h / 2))
        p2 = (rd(wtop / 2), rd(h / 2))
        p3 = (rd(wbtn / 2), rd(-h / 2))
        p4 = (rd(-wbtn / 2), rd(-h / 2))

        if return_solid is False:
            outer_curve, inner_curve, disconnected = [p1, p2, p3, p4], None, False
        else:
            tftop = sec.t_fbtn
            tfbtn = sec.t_fbtn
            tw = sec.t_w
            p5 = (rd(-wtop / 2 + tw), rd(h / 2 - tftop))
            p6 = (rd(wtop / 2 - tw), rd(h / 2 - tftop))
            p7 = (rd(wbtn / 2 - tw), rd(-h / 2 + tfbtn))
            p8 = (rd(-wbtn / 2 + tw), rd(-h / 2 + tfbtn))

            outer_curve = [p1, p2, p3, p4]
            inner_curve = [p5, p6, p7, p8]
            disconnected = False

        return outer_curve, inner_curve, disconnected

    @classmethod
    def tubular(cls, sec, return_solid):
        """

        :param sec:
        :param return_solid:
        :type sec: ada.Section
        :type return_solid: bool
        """
        return None, None, None

    @classmethod
    def circular(cls, sec, return_solid=None):
        """

        :param sec:
        :param return_solid:
        :type sec: ada.Section
        :type return_solid:
        """
        return sec.r, None, False

    @classmethod
    def flatbar(cls, sec, return_solid=None):
        """

        :param sec:
        :param return_solid:
        :type sec: ada.Section
        :return:
        """
        outer_curve = [
            (-sec.w_top / 2, sec.h / 2),
            (sec.w_top / 2, sec.h / 2),
            (sec.w_top / 2, -sec.h / 2),
            (-sec.w_top / 2, -sec.h / 2),
        ]
        inner_curve = None
        disconnected = False
        return outer_curve, inner_curve, disconnected

    @classmethod
    def gensec(cls, sec, return_solid=None):
        """

        :param sec:
        :param return_solid:
        :type sec: ada.Section
        :return:
        """
        import numpy as np

        radius = np.sqrt(sec.properties.Ax / np.pi)
        inner_curve = None
        disconnected = False
        return radius, inner_curve, disconnected

    @classmethod
    def channel(cls, sec, return_solid=None):
        """

        :param sec:
        :param return_solid:
        :type sec: ada.Section
        :return:
        """
        outer_curve = [
            (sec.w_top, sec.h / 2 - sec.t_ftop),
            (sec.w_top, sec.h / 2),
            (0, sec.h / 2),
            (0, -sec.h / 2),
            (sec.w_top, -sec.h / 2),
            (sec.w_top, -sec.h / 2 + sec.t_fbtn),
            (sec.t_w, -sec.h / 2 + sec.t_fbtn),
            (sec.t_w, sec.h / 2 - sec.t_fbtn),
        ]
        inner_curve = None
        disconnected = False
        return outer_curve, inner_curve, disconnected

    @staticmethod
    def build_representation(beam, solid=True):
        """
        Build a 3d model by

        :param beam:
        :param solid:
        :type beam: ada.Beam
        :return:
        """
        from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
        from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_ThruSections
        from OCC.Core.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
        from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Wire

        from ada.core.utils import tuple_minus
        from ada.occ.utils import face_to_wires

        from .categories import SectionCat

        xdir, ydir, zdir = beam.ori
        ydir_neg = tuple_minus(ydir) if beam.section.type not in SectionCat.angular else tuple(ydir)

        sec = beam.section.cross_sec_shape(
            solid,
            origin=tuple(beam.n1.p.astype(float)),
            xdir=ydir_neg,
            normal=tuple(xdir),
        )
        tap = beam.taper.cross_sec_shape(
            solid,
            origin=tuple(beam.n2.p.astype(float)),
            xdir=ydir_neg,
            normal=tuple(xdir),
        )

        def through_section(sec_a, sec_b, solid_):
            generator_sec = BRepOffsetAPI_ThruSections(solid_, False)
            generator_sec.AddWire(sec_a)
            generator_sec.AddWire(sec_b)
            generator_sec.Build()
            return generator_sec.Shape()

        if type(sec) is TopoDS_Face:
            sec_result = face_to_wires(sec)
            tap_result = face_to_wires(tap)
        elif type(sec) is TopoDS_Wire:
            sec_result = [sec]
            tap_result = [tap]
        else:
            assert isinstance(sec, list)
            sec_result = sec
            tap_result = tap

        shapes = list()
        for s_, t_ in zip(sec_result, tap_result):
            shapes.append(through_section(s_, t_, solid))

        if beam.section.type in SectionCat.box + SectionCat.tubular + SectionCat.rhs + SectionCat.shs and solid is True:
            cut_shape = BRepAlgoAPI_Cut(shapes[0], shapes[1]).Shape()
            shape_upgrade = ShapeUpgrade_UnifySameDomain(cut_shape, False, True, False)
            shape_upgrade.Build()
            return shape_upgrade.Shape()

        if len(shapes) == 1:
            return shapes[0]
        else:
            result = shapes[0]
            for s in shapes[1:]:
                result = BRepAlgoAPI_Fuse(result, s).Shape()
            return result
