import logging
import math

from ada.core.utils import roundoff as rd


class GeneralProperties:
    def __init__(
        self,
        ax=None,
        ix=None,
        iy=None,
        iz=None,
        iyz=None,
        wxmin=None,
        wymin=None,
        wzmin=None,
        shary=None,
        sharz=None,
        scheny=None,
        schenz=None,
        sy=None,
        sz=None,
        sfy=1,
        sfz=1,
        cy=None,
        cz=None,
        parent=None,
    ):
        self._Ax = ax
        self._Ix = ix
        self._Iy = iy
        self._Iz = iz
        self._Iyz = iyz
        self._Wxmin = wxmin
        self._Wymin = wymin
        self._Wzmin = wzmin
        self._Shary = shary
        self._Sharz = sharz
        self._Scheny = scheny
        self._Schenz = schenz
        self._Sy = sy
        self._Sz = sz
        self._Sfy = sfy
        self._Sfz = sfz
        self._Cy = cy
        self._Cz = cz
        self._parent = parent

    def edit(self, parent):
        self._parent = parent

    def _calc_box(self):
        """
        Calculate box cross section properties
        """

        sfy = 1.0
        sfz = 1.0
        s = self.parent
        self._Ax = s.w_btn * s.t_fbtn + s.w_top * s.t_ftop + s.t_w * (s.h - (s.t_fbtn + s.t_ftop)) * 2
        a = s.t_fbtn
        b = (s.h + s.t_fbtn - s.t_ftop) / 2
        c = s.h - s.t_ftop
        d = s.h - s.t_fbtn - s.t_ftop
        e = s.w_top * s.t_fbtn
        f = s.w_top * s.t_ftop
        g = s.t_w * d
        h = (e * a + f * c + 2 * b * g) / self.Ax
        ha = s.h - (s.t_fbtn + s.t_ftop) / 2.0
        hb = s.w_top - s.t_w

        self._Ix = 4 * (ha * hb) ** 2 / (hb / s.t_fbtn + hb / s.t_ftop + 2 * ha / s.t_w)
        self._Iy = (
            (s.w_top * (s.t_fbtn ** 3 + s.t_ftop ** 3) + 2 * s.t_w * d ** 3) / 12
            + e * (h - a) ** 2
            + f * (c - h) ** 2
            + 2 * g * (b - h) ** 2
        )
        self._Iz = ((s.t_fbtn + s.t_ftop) * s.w_top ** 3 + 2 * d * s.t_w ** 3) / 12 + (g * hb ** 2) / 2
        self._Iyz = 0
        self._Wxmin = self.Ix * (hb + ha) / (ha * hb)
        self._Wymin = self.Iy / max(s.h - h, h)
        self._Wzmin = 2 * self._Iz / s.w_top
        self._Sy = e * (h - a) + s.t_w * (h - s.t_fbtn) ** 2
        self._Sz = (s.t_fbtn + s.t_ftop) * s.w_top ** 2 / 8 + g * hb / 2
        self._Shary = (self.Iz / self.Sz) * 2 * s.t_w * sfy
        self._Sharz = (self.Iy / self.Sy) * 2 * s.t_w * sfz
        self._Scheny = 0
        self._Schenz = c - h - s.t_fbtn * ha / (s.t_fbtn + s.t_ftop)
        self._Cy = s.w_top / 2
        self._Cz = h

    def _calc_isec(self):
        """
        Calculate I/H cross section properties
        """

        sfy = 1.0
        sfz = 1.0
        s = self.parent
        hz = s.h
        bt = s.w_top
        tt = s.t_ftop
        ty = s.t_w
        bb = s.w_btn
        tb = s.t_fbtn

        self._Ax = bt * tt + ty * (hz - (tb + tt)) + bb * tb
        hw = hz - tt - tb
        a = tb + hw + tt / 2
        b = tb + hw / 2
        c = tb / 2

        z = (tb * tt * a + hw * ty * b + bb * tb * c) / self.Ax
        tra = (bb * tb ** 3) / 12 + bt * tt * (hz - tt / 2 - z) ** 2
        trb = (ty * hw ** 3) / 12 + ty * hw * (tb + hw / 2 - z) ** 2
        trc = (bb * tb ** 3) / 12 + bb * tb * (tb / 2 - z) ** 2

        if tt == ty and tt == tb:
            self._Ix = (tt ** 3) * (hw + bt + bb - 1.2 * tt) / 3
            self._Wxmin = self.Ix / tt
        else:
            self._Ix = 1.3 * (bt * tt ** 3 + hw * ty ** 3 + bb * tb ** 3) / 3
            self._Wxmin = self.Ix / max(tt, ty, tb)

        self._Iy = tra + trb + trc
        self._Iz = (tb * bb ** 3 + hw * ty ** 3 + tt * bt ** 3) / 12
        self._Iyz = 0
        self._Wymin = self.Iy / max(hz - z, z)
        self._Wzmin = 2 * self.Iz / max(bb, bt)

        # Sy should be checked. Confer older method implementation.
        self._Sy = (((tt * bt) ** 2) * (hw / 2 + tt / 2)) * 2
        self._Sz = (tt * bt ** 2 + tb * bb ** 2 + hw * ty ** 2) / 8
        self._Shary = (self.Iz / self.Sz) * (tb + tt) * sfy
        self._Sharz = (self.Iy / self.Sy) * ty * sfz
        self._Scheny = 0
        self._Schenz = ((hz - tt / 2) * tt * bt ** 3 + (tb ** 2) * (bb ** 3) / 2) / (tt * bt ** 3 + tb * bb ** 3) - z
        self._Cy = bb / 2
        self._Cz = z

    def _calc_angular(self):
        """
        Calculate L cross section properties
        """
        s = self.parent
        posweb = True
        hz = s.h
        ty = s.t_w
        tz = s.t_fbtn
        by = s.w_btn
        sfy = 1.0
        sfz = 1.0

        r = 0
        hw = hz - tz
        b = tz - hw / 2
        c = tz / 2
        piqrt = math.atan(1.0)
        self._Ax = ty * hw + by * tz + (1 - piqrt) * r ** 2
        y = (hw * ty ** 2 + tz * by ** 2) / (2 * self.Ax)
        z = (hw * b * ty + tz * by * c) / self.Ax
        d = 6 * r + 2 * (ty + tz - math.sqrt(4 * r * (2 * r + ty + tz) + 2 * ty * tz))
        e = hw + tz - z
        f = hw - e
        ri = y - ty
        rj = by - y
        rk = ri + 0.5 * ty
        rl = z - c

        if tz >= ty:
            h = hw
        else:
            raise ValueError("Currently not implemented this yet")

        self._Iy = (ty * hw ** 3 + by * tz ** 3) / 12 + hw * ty * (b - z) ** 2 + by * tz * (z - c) ** 2
        self._Iz = (hw * ty ** 3 + tz * by ** 3) / 12 + hw * ty * rk ** 2 + tz * by * (by / 2 - y) ** 2
        self._Iyz = (rl * tz / 2) * (y ** 2 - rj ** 2) - (rk * ty / 2) * (e ** 2 - f ** 2)

        # This is incorrect. Should find this in my old calculation method.
        self._Ix = self.Iy + self.Iz

        self._Wxmin = self.Ix / d
        self._Wymin = self.Iy / max(z, hz - h)
        self._Wzmin = self.Iz / max(y, rj)
        self._Sy = (ty * e ** 2) / 2
        self._Sz = (tz * rj ** 2) / 2
        self._Shary = (self.Iz * tz / self.Sz) * sfy
        self._Sharz = (self.Iy * tz / self.Sy) * sfz

        if posweb:
            self._Iyz = -self._Iyz
            self._Scheny = rk
            self._Cy = by - y
        else:
            self._Scheny = -rk
            self._Cy = y
        self._Schenz = -rl
        self._Cz = z

    def _calc_tubular(self):
        """
        Calculate Tubular cross section properties
        """
        s = self.parent
        dy = s.r * 2
        t = s.wt
        sfy = 1.0
        sfz = 1.0
        di = dy - 2 * t
        self._Ax = math.pi * s.r ** 2 - math.pi * (s.r - t) ** 2
        self._Ix = 0.5 * math.pi * ((dy / 2) ** 4 - (di / 2) ** 4)
        self._Iy = self.Ix / 2
        self._Iz = self.Iy
        self._Iyz = 0
        self._Wxmin = 2 * self.Ix / dy
        self._Wymin = 2 * self.Iy / dy
        self._Wzmin = 2 * self.Iz / dy
        self._Sy = (dy ** 3 - di ** 3) / 12
        self._Sz = self.Sy
        self._Shary = (2 * self.Iz * t / self.Sy) * sfy
        self._Sharz = (2 * self.Iy * t / self.Sz) * sfz
        self._Scheny = 0
        self._Schenz = 0

    def _calc_circular(self):

        s = self.parent
        self._Ax = math.pi * s.r ** 2
        self._Iy = (math.pi * s.r ** 4) / 4
        self._Iz = self._Iy

    def _calc_flatbar(self):
        s = self.parent
        self._Ax = (s.w_btn + s.w_top) / 2 * s.h
        self._Iy = s.w_top * s.h ** 3 / 12
        self._Iz = s.h * s.w_top ** 3 / 12

    def _calc_channel(self):
        """

        :return:
        """
        posweb = True
        s = self.parent
        hz = s.h
        ty = s.t_w
        tz = s.t_fbtn
        by = s.w_btn
        sfy = 1.0
        sfz = 1.0

        a = hz - 2 * tz
        self._Ax = 2 * by * tz + a * ty
        y = (2 * tz * by ** 2 + a * ty ** 2) / (2 * self.Ax)
        self._Iy = (ty * a ** 3) / 12 + 2 * ((by * tz ** 3) / 12 + by * tz * ((a + tz) / 2) ** 2)

        if tz == ty:
            self._Ix = ty ** 3 * (2 * by + a - 2.6 * ty) / 3
            self._Wxmin = self.Ix / self.Iy
        else:
            self._Ix = 1.12 * (2 * by * tz ** 3 + a * ty ** 3) / 3
            self._Wxmin = self._Ix / max(tz, ty)

        self._Iz = (
            2 * ((tz * by ** 3) / 12 + tz * by * (by / 2 - y) ** 2) + (a * ty ** 3) / 12 + a * ty * (y - ty / 2) ** 2
        )
        self._Iyz = 0
        self._Wymin = 2 * self.Iy / hz
        self._Wzmin = self.Iz / max(by - y, y)
        self._Sy = by * tz * (tz + a) / 2 + (ty * a ** 2) / 8
        self._Sz = tz * (by - y) ** 2
        self._Shary = (self._Iz / self.Sz) * (2 * tz) * sfy
        self._Sharz = (self._Iy / self.Sy) * (2 * ty) * sfz

        if tz == ty:
            q = ((by - ty / 2) ** 2) * ((hz - tz) ** 2) * tz / 4 * self.Iy
        else:
            q = ((by - ty / 2) ** 2) * tz / (2 * (by - ty / 2) * tz + (hz - tz) * ty / 3)

        if posweb:
            self._Scheny = y - ty / 2 + q
            self._Cy = by
        else:
            self._Scheny = -(y - ty / 2 + q)
            self._Cy = by - y

        self._Schenz = 0

    def calculate(self):
        """
        Calculates the cross section properties based on the parent section.

        A large parts of the calculations are based on the document

            DNVGL. (2011). Appendix B Section properties & consistent units Table of Contents. I.

        Which in turn bases most (if not all) formulas on the work in

            * W. Beitz, K.H. Küttner: "Dubbel, Taschenbuch für den Maschinenbau" 17. Auflage (17th ed.)
              Springer-Verlag 1990
            * Arne Selberg: "Stålkonstruksjoner" Tapir 1972
            * S. Timoshenko: "Strength of Materials, Part I, Elementary Theory and Problems" Third Edition 1995 D.
              Van Nostrand Company Inc.

        """

        if self.parent.type in SectionCat.circular:
            self._calc_circular()
        elif SectionCat.is_i_profile(self.parent.type):
            self._calc_isec()
        elif SectionCat.is_box_profile(self.parent.type):
            self._calc_box()
        elif self.parent.type in SectionCat.general:
            logging.error("Calculation of general section")
            pass  # it is known
        elif self.parent.type in SectionCat.tubular:
            self._calc_tubular()
        elif SectionCat.is_hp_profile(self.parent.type):
            self._calc_angular()
        elif SectionCat.is_channel_profile(self.parent):
            self._calc_channel()
        elif SectionCat.is_flatbar(self.parent.type):
            self._calc_flatbar()
        else:
            raise Exception(
                f'section type "{self.parent.type}" is not yet supported in the cross section parameter calculations'
            )

    @property
    def parent(self):
        """

        :return:
        :rtype: ada.Section
        """
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def Ax(self):
        """

        :return: Area of cross section
        """
        if self._Ax is None:
            self.calculate()
        return self._Ax

    @property
    def Ix(self):
        """
        Torsional moment of inertia about shear centre
        :return:
        """
        if self._Ix is None:
            self.calculate()
        return self._Ix

    @Ix.setter
    def Ix(self, value):
        if value <= 0.0:
            raise ValueError("Value cannot be zero or non-positive")
        self._Ix = value

    @property
    def Iy(self):
        """

        :return: Moment of inertia about y-axis
        """
        if self._Iy is None:
            self.calculate()
        return self._Iy

    @Iy.setter
    def Iy(self, value):
        if value <= 0.0:
            raise ValueError("Value cannot be zero or non-positive")
        self._Iy = value

    @property
    def Iz(self):
        """

        :return: Moment of inertia about z-axis
        """
        if self._Iz is None:
            self.calculate()
        return self._Iz

    @Iz.setter
    def Iz(self, value):
        if value <= 0.0:
            raise ValueError("Value cannot be zero or non-positive")
        self._Iz = value

    @property
    def Iyz(self):
        """

        :return: Product of inertia about y- and z-axes
        """
        if self._Iyz is None:
            self.calculate()
        return self._Iyz

    @Iyz.setter
    def Iyz(self, value):
        if value <= 0.0:
            raise ValueError("Value cannot be zero or non-positive")
        self._Iyz = value

    @property
    def Wxmin(self):
        """

        :return: Minimum torsional sectional modulus about shear centre
        """
        if self._Wxmin is None:
            self.calculate()
        return self._Wxmin

    @property
    def Wymin(self):
        """

        :return: Minimum sectional modulus about y-axis
        """
        if self._Wymin is None:
            self.calculate()
        return self._Wymin

    @property
    def Wzmin(self):
        """

        :return: Minimum sectional modulus about z-axis
        """
        if self._Wzmin is None:
            self.calculate()
        return self._Wzmin

    @property
    def Shary(self):
        """

        :return: Shear area in the direction of y-axis
        """
        if self._Shary is None:
            self.calculate()
        return self._Shary

    @property
    def Sharz(self):
        """

        :return: Shear area in the direction of z-axis
        """
        if self._Sharz is None:
            self.calculate()
        return self._Sharz

    @property
    def Scheny(self):
        """

        :return: Shear centre location y-component
        """
        if self._Scheny is None:
            self.calculate()
        return self._Scheny

    @property
    def Schenz(self):
        """

        :return: Shear centre location z-component
        """
        if self._Scheny is None:
            self.calculate()
        return self._Schenz

    @property
    def Sy(self):
        """

        :return: Static area moment about y-axis
        """
        return self._Sy

    @property
    def Sz(self):
        """

        :return: Static area moment about z-axis
        """
        return self._Sz

    @property
    def Cz(self):
        return self._Cz

    @property
    def Sfy(self):
        """
        :return: Centroid location from bottom right corner y-component
        """
        return self._Sfy

    @property
    def Sfz(self):
        """
        :return: Centroid location from bottom right corner z-component
        """
        return self._Sfz

    def __eq__(self, other):
        for key, val in self.__dict__.items():
            if "parent" in key:
                continue
            if other.__dict__[key] != val:
                return False

        return True


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

        from ada.core.utils import face_to_wires, tuple_minus
        from ada.sections import SectionCat

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
