from typing import Callable, List, Tuple

import numpy as np

from ada import Assembly, Beam, Material, Node, Part, Pipe, Plate, PrimSphere, Section
from ada.core.clash_check import penetration_check
from ada.core.constants import X, Y, Z
from ada.core.utils import Counter
from ada.fem import Bc, FemSet
from ada.materials.metals import CarbonSteel

bm_name = Counter(1, "bm")
pl_name = Counter(1, "pl")
floor_name = Counter(1, "floor")


class ReinforcedFloor(Part):
    def __init__(
        self,
        name,
        points: List[tuple],
        pl_thick: float,
        spacing=0.2,
        s_type="HP140x8",
        stringer_dir="X",
        use3dnodes=True,
        **kwargs,
    ):
        super(ReinforcedFloor, self).__init__(name)
        plate = self.add_plate(Plate(name + "_pl", points, pl_thick, use3dnodes=use3dnodes, **kwargs))

        # Calculate number of stringers
        bbox = plate.bbox
        xmin, xmax = bbox[0]
        ymin, ymax = bbox[1]

        if stringer_dir == "Y":
            snum = int((xmax - xmin) / spacing) - 1
        else:
            snum = int((ymax - ymin) / spacing) - 1

        origin = plate.poly.placement.origin
        z = origin[2]
        x = xmin + spacing
        y = ymin + spacing
        for i in range(0, snum):
            if stringer_dir == "Y":
                p1 = (x, ymin, z)
                p2 = (x, ymax, z)
                x += spacing
            else:
                p1 = (xmin, y, z)
                p2 = (xmax, y, z)
                y += spacing
            self.add_beam(Beam(next(bm_name), p1, p2, sec=s_type))


class SimpleStru(Part):
    class Params:
        w = None
        l = None
        h = None

    def __init__(self, name, origin=(0, 0, 0), w=5, l=5, h=3, gsec="IPE200", csec="HEB200", pl_thick=10e-3):
        super(SimpleStru, self).__init__(name, origin)
        self.Params.w = w
        self.Params.h = h
        self.Params.l = l

        # Define the 5 corner points of each storey
        c1, c2, c3, c4 = self.c1, self.c2, self.c3, self.c4

        # Define the relationship of corners that make up the 4 support beams
        beams = [(c1, c2), (c2, c3), (c3, c4), (c4, c1)]

        z0 = origin[2]
        sec = Section(gsec, from_str=gsec, parent=self)
        for elev in [z0, h]:
            for p1, p2 in beams:
                self.add_beam(Beam(next(bm_name), n1=p1(elev), n2=p2(elev), sec=sec, jusl="TOP"))
            points = [c1(elev), c2(elev), c3(elev), c4(elev)]
            self.add_part(ReinforcedFloor(next(floor_name), points, pl_thick))

        # Columns
        columns = [(c1(z0), c1(h)), (c2(z0), c2(h)), (c3(z0), c3(h)), (c4(z0), c4(h))]
        for p1, p2 in columns:
            self.add_beam(Beam(next(bm_name), n1=p1, n2=p2, sec=csec))

    def c1(self, z) -> tuple:
        return 0, 0, z

    def c2(self, z) -> tuple:
        return self.Params.w, 0, z

    def c3(self, z) -> tuple:
        return self.Params.w, self.Params.l, z

    def c4(self, z) -> tuple:
        return 0, self.Params.l, z

    def add_bcs(self):
        funcs: List[Callable] = [self.c1, self.c2, self.c3, self.c4]
        fem_set_btn = self.fem.add_set(FemSet("fix", [], FemSet.TYPES.NSET))
        nodes: List[Node] = []
        for bc_loc in funcs:
            nodes += self.fem.nodes.get_by_volume(bc_loc(self.placement.origin[2]))
        fem_set_btn.add_members(nodes)
        self.fem.add_bc(Bc("bc_fix", fem_set_btn, [1, 2, 3]))


def make_it_complex():
    pm = SimpleStru("ParametricModel")
    a = Assembly("ParametricSite") / pm

    elev = pm.Params.h - 0.4
    offset_y = 0.4
    pipe1 = Pipe(
        "Pipe1",
        [
            (0, offset_y, elev),
            (pm.Params.w + 0.4, offset_y, elev),
            (pm.Params.w + 0.4, pm.Params.l + 0.4, elev),
            (pm.Params.w + 0.4, pm.Params.l + 0.4, 0.4),
            (0, pm.Params.l + 0.4, 0.4),
        ],
        Section("PSec1", "PIPE", r=0.1, wt=10e-3),
    )

    pipe2 = Pipe(
        "Pipe2",
        [
            (0.5, offset_y + 0.5, elev + 1.4),
            (0.5, offset_y + 0.5, elev),
            (0.2 + pm.Params.w, offset_y + 0.5, elev),
            (0.2 + pm.Params.w, pm.Params.l + 0.4, elev),
            (0.2 + pm.Params.w, pm.Params.l + 0.4, 0.6),
            (0, pm.Params.l + 0.4, 0.6),
        ],
        Section("PSec2", "PIPE", r=0.05, wt=5e-3),
    )
    pm.add_part(Part("Piping") / [pipe1, pipe2])
    for p in pm.parts.values():
        if type(p) is ReinforcedFloor:
            penetration_check(p)

    return a


class EquipmentTent(Part):
    def __init__(
        self,
        name,
        mass: float,
        cog: Tuple[float, float, float],
        legs=4,
        height=2,
        width=3,
        length=3,
        sec_str="BG200x200x30x30",
        eq_mat=Material("EqMatSoft", CarbonSteel("S355", E=2.1e9)),
    ):
        """

        :param name:
        :param mass:
        :param cog:
        :param legs: can be either 3 or 4 legs
        :param height:
        :param width:
        :param length: Length is along the Y-axis
        """
        super(EquipmentTent, self).__init__(name=name)

        eq_bm = Counter(1, f"{name}_bm")
        cognp = np.array(cog)
        corner_index = [(-1, -1), (-1, 1), (1, 1), (1, -1)]
        if legs == 3:
            corner_index = [(-1, -1), (1, -1), (0.5, 1)]

        mid_points = []
        btn_points = []

        for sX, sY in corner_index:
            p = cognp + sX * np.array(X) * width + sY * np.array(Y) * length - np.array(Z) * height / 2
            mid_points.append(p)
            p = cognp + sX * np.array(X) * width + sY * np.array(Y) * length - np.array(Z) * height
            btn_points.append(p)

        vertical_legs = []
        for btnp, midp in zip(btn_points, mid_points):
            bm = Beam(next(eq_bm), btnp, midp, sec_str, eq_mat)
            vertical_legs.append(bm)
            self.add_beam(bm)

        horizontal_members = []
        for bs, be in zip(mid_points[:-1], mid_points[1:]):
            bm = Beam(next(eq_bm), bs, be, sec_str, eq_mat)
            horizontal_members.append(bm)
            self.add_beam(bm)

        bm = Beam(next(eq_bm), mid_points[-1], mid_points[0], sec_str, eq_mat)
        horizontal_members.append(bm)
        self.add_beam(bm)

        eq_braces = []
        for midp in mid_points:
            bm = Beam(next(eq_bm), midp, cog, sec_str, eq_mat)
            eq_braces.append(bm)
            self.add_beam(bm)

        self.add_shape(PrimSphere(f"{name}_cog", cog, radius=(width + length) / 6, mass=mass))
        self._centre_of_gravity = cog
        self._mass = mass
