from ada import Beam, Part, Plate, Section
from ada.core.utils import Counter

bm_name = Counter(1, "bm")
pl_name = Counter(1, "pl")
floor_name = Counter(1, "floor")


class ReinforcedFloor(Part):
    def __init__(self, name, plate, spacing=0.2, s_type="HP140x8", stringer_dir="X"):
        """

        :param name:
        :param plate:
        :param spacing:
        :param s_type:
        :param stringer_dir:
        :type plate: ada.Plate
        """
        super(ReinforcedFloor, self).__init__(name)
        self.add_plate(plate)

        # Calculate number of stringers
        bbox = plate.bbox
        xmin, xmax = bbox[0]
        ymin, ymax = bbox[1]
        zmin, zmax = bbox[2]

        if stringer_dir == "Y":
            snum = int((xmax - xmin) / spacing) - 1
        else:
            snum = int((ymax - ymin) / spacing) - 1

        origin = plate.poly.origin
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

    def penetration_check(self):
        import numpy as np

        from ada import PipeSegStraight, PrimCyl

        a = self.get_assembly()
        cog = self.nodes.vol_cog
        normal = self._lz
        for p in a.get_all_subparts():
            for pipe in p.pipes:
                for segment in pipe.segments:
                    if type(segment) is PipeSegStraight:
                        assert isinstance(segment, PipeSegStraight)
                        p1, p2 = segment.p1, segment.p2
                        v1 = (p1.p - cog) * normal
                        v2 = (p2.p - cog) * normal
                        if np.dot(v1, v2) < 0:
                            self.add_penetration(
                                PrimCyl(f"{p.name}_{pipe.name}_{segment.name}_pen", p1.p, p2.p, pipe.section.r + 0.1)
                            )


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
            plate = Plate(next(pl_name), [c1(elev), c2(elev), c3(elev), c4(elev)], pl_thick, use3dnodes=True)
            self.add_part(ReinforcedFloor(next(floor_name), plate))

        # Columns
        columns = [(c1(z0), c1(h)), (c2(z0), c2(h)), (c3(z0), c3(h)), (c4(z0), c4(h))]
        for p1, p2 in columns:
            self.add_beam(Beam(next(bm_name), n1=p1, n2=p2, sec=csec))

    def c1(self, z):
        return 0, 0, z

    def c2(self, z):
        return self.Params.w, 0, z

    def c3(self, z):
        return self.Params.w, self.Params.l, z

    def c4(self, z):
        return 0, self.Params.l, z

    def add_bcs(self):
        from ada.fem import Bc, FemSet

        for i, bc_loc in enumerate([self.c1, self.c2, self.c3, self.c4]):
            fem_set_btn = FemSet(f"fix{i}", [], "nset")
            self.fem.add_set(fem_set_btn, p=bc_loc(self._origin[2]))
            self.fem.add_bc(Bc(f"bc_fix{i}", fem_set_btn, [1, 2, 3]))


def make_it_complex():
    from ada import Assembly, Pipe, Section

    a = Assembly("ParametricSite")

    pm = SimpleStru("ParametricModel")
    a.add_part(pm)

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
            p.penetration_check()

    return a
