from typing import Callable, Tuple

import numpy as np

import ada
from ada import Assembly, Beam, Material, Node, Part, Pipe, Plate, PrimSphere, Section
from ada.api.beams.helpers import Justification, get_offset_from_justification
from ada.api.transforms import Placement
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
        points: list[tuple],
        pl_thick: float,
        spacing=0.4,
        s_type="HP140x8",
        stringer_dir="X",
        use3dnodes=True,
        **kwargs,
    ):
        super(ReinforcedFloor, self).__init__(name, **kwargs)
        if use3dnodes:
            plate = Plate.from_3d_points(
                name + "_pl",
                points,
                pl_thick,
            )
        else:
            plate = Plate(name + "_pl", points, pl_thick, placement=self.placement)

        self.add_plate(plate)

        # Calculate number of stringers

        (xmin, ymin, zmin), (xmax, ymax, zmax) = plate.bbox().minmax

        if stringer_dir == "Y":
            snum = int((xmax - xmin) / spacing) - 1
        else:
            snum = int((ymax - ymin) / spacing) - 1

        z = plate.poly.origin[2]

        tot_spacing = snum * spacing
        diff = xmax - xmin - tot_spacing
        x = diff / 2
        y = diff / 2
        for i in range(0, snum + 1):
            if stringer_dir == "Y":
                p1 = (x, ymin, z)
                p2 = (x, ymax, z)
                x += spacing
            else:
                p1 = (xmin, y, z)
                p2 = (xmax, y, z)
                y += spacing
            self.add_beam(Beam(f"{name}_{next(bm_name)}", p1, p2, sec=s_type))


class SimpleStru(Part):
    class Params:
        w = None
        l = None
        h = None

    def __init__(
        self,
        name,
        w=5,
        l=5,
        h=3,
        gsec="IPE200",
        csec="HEB200",
        pl_thick=10e-3,
        placement=Placement(),
        add_bottom_floor=True,
    ):
        super(SimpleStru, self).__init__(name, placement=placement)
        self.Params.w = w
        self.Params.h = h
        self.Params.l = l

        # Define the 4 corner points of each storey
        c1, c2, c3, c4 = self.c1, self.c2, self.c3, self.c4

        # Define the relationship of corners that make up the 4 support beams
        beams = [(c1, c2), (c2, c3), (c3, c4), (c4, c1)]

        z0 = 0
        z1 = h
        sec = Section(gsec, from_str=gsec, parent=self)
        self._elevations = []
        if add_bottom_floor:
            self._elevations += [z0]
        self._elevations += [z1]

        for elev in self._elevations:
            for p1, p2 in beams:
                bm = self.add_beam(Beam(next(bm_name), n1=p1(elev), n2=p2(elev), sec=sec))
                ecc = get_offset_from_justification(bm, Justification.TOS)
                bm.e1 = bm.e2 = ecc
            points = [c1(elev), c2(elev), c3(elev), c4(elev)]
            p = self.add_part(ReinforcedFloor(next(floor_name), points, pl_thick))
            self.add_group("floors", [p])

        # Columns
        z0 -= 0.5
        self._btn_col = z0
        columns = [(c1(z0), c1(h)), (c2(z0), c2(h)), (c3(z0), c3(h)), (c4(z0), c4(h))]
        for p1, p2 in columns:
            bm = self.add_beam(Beam(next(bm_name), n1=p1, n2=p2, sec=csec))
            self.add_group("columns", [bm])

    def c1(self, z) -> tuple:
        return 0, 0, z

    def c2(self, z) -> tuple:
        return self.Params.w, 0, z

    def c3(self, z) -> tuple:
        return self.Params.w, self.Params.l, z

    def c4(self, z) -> tuple:
        return 0, self.Params.l, z

    def add_bcs(self):
        funcs: list[Callable] = [self.c1, self.c2, self.c3, self.c4]
        fem_set_btn = self.fem.add_set(FemSet("fix", [], FemSet.TYPES.NSET))
        nodes: list[Node] = []
        col_btn_offset = np.array([0, 0, self._btn_col])
        for bc_loc in funcs:
            location = self.placement.origin + bc_loc(self._elevations[0]) + col_btn_offset
            nodes += self.fem.nodes.get_by_volume(location)

        fem_set_btn.add_members(nodes)
        if len(fem_set_btn.members) == 0:
            raise ValueError("Number of Boundary Conditions cannot be zero")

        self.fem.add_bc(Bc("bc_fix", fem_set_btn, [1, 2, 3]))


def simplestru_with_cutouts() -> ada.Assembly:
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

        self.add_group("vertical_members", vertical_legs)
        self.add_group("horizontal_members", horizontal_members)
        self.add_group("braces", eq_braces)

        self.add_shape(PrimSphere(f"{name}_cog", cog, radius=(width + length) / 6, mass=mass))
        self._centre_of_gravity = cog
        self._mass = mass

    def _on_import(self):
        print("Evaluate footing based on existing structure")
