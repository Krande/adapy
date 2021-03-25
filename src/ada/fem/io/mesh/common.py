import functools
import logging
import os
from itertools import chain

import gmsh
import numpy as np

from ada import Node, Plate
from ada.core.containers import Nodes
from ada.core.utils import clockwise, intersect_calc, roundoff, vector_length
from ada.fem import Elem, FemSection, FemSet
from ada.fem.containers import FemElements

gmsh_map = {"Triangle 3": "S3", "Quadrilateral 4": "S4R"}


class GMesh:
    """
    A class wrapping around the gmsh python mesh library

    links to gmsh tips:

        https://forum.freecadweb.org/viewtopic.php?style=4&f=18&t=20796&start=30

    :param work_dir: Work directory
    """

    mesh_map = {"bm1", "B31", "bm2", "B32"}

    def __init__(self, part, work_dir="gmsh", tol=1e-5):
        self._part = part
        self._work_dir = os.path.abspath(work_dir)
        self._tol = tol
        self._bm_map = dict()
        self._pl_map = dict()

    def mesh(
        self,
        size=0.1,
        order=1,
        max_dim=2,
        interactive=False,
        mesh_algo=8,
        sh_int_points=5,
    ):
        """

        :param size:
        :param order:
        :param max_dim:
        :param interactive:
        :param mesh_algo:
        :param sh_int_points:
        :return:
        """

        part = self._part
        pl_in = [pl_ for p in part.get_all_subparts() for pl_ in p.plates] + [pl for pl in part.plates]
        bm_in = [bm_ for p in part.get_all_subparts() for bm_ in p.beams] + [bm for bm in part.beams]
        try:
            gmsh.finalize()
        except BaseException as e:
            logging.debug(e)
        gmsh.initialize()
        gmsh.option.setNumber("General.Terminal", 1)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)
        gmsh.option.setNumber("Mesh.Algorithm", mesh_algo)
        gmsh.option.setNumber("Mesh.ElementOrder", order)

        list(
            map(
                functools.partial(
                    self._create_plate_geom,
                    beams=bm_in,
                    size=size,
                    interactive=interactive,
                ),
                pl_in,
            )
        )
        gmsh.model.geo.synchronize()

        list(
            map(
                functools.partial(self._create_bm_geom, size=size),
                filter(lambda x: x not in [b for b in self._bm_map.values()], bm_in),
            )
        )
        gmsh.model.geo.synchronize()

        if len(pl_in) > 0:
            gmsh.model.mesh.setRecombine(2, 1)

        gmsh.model.mesh.generate(max_dim)
        gmsh.model.mesh.removeDuplicateNodes()

        if interactive:
            gmsh.fltk.run()

        nodes = list(gmsh.model.mesh.getNodes(-1, -1))

        # Extract Gmsh model information and import the data into a FEM model
        self._part.fem._nodes = Nodes(list(map(self.get_mesh_nodes, nodes[0])), parent=self._part.fem)
        bm_elems = map(functools.partial(self.get_beam_elements, order=order), self._bm_map.keys())
        pl_elems = map(functools.partial(self.get_shell_elements, order=order), self._pl_map.keys())
        self._part.fem._elements = FemElements(
            chain.from_iterable(list(bm_elems) + list(pl_elems)), fem_obj=self._part.fem
        )
        self._part.fem.elements.renumber()

        gmsh.finalize()

    def mesh_shape(self, shp, size=0.1, order=1, max_dim=3, mesh_algo=8, interactive=False):
        """



        :param shp: ADA shape object
        :param size: Size of mesh
        :param order: Polynomial order
        :param max_dim: Maximum dimensions (1, 2 or 3).
        :param mesh_algo: 2D mesh algorithm (1: MeshAdapt, 2: Automatic, 5: Delaunay, 6: Frontal-Delaunay, 7: BAMG,
                          8: Frontal-Delaunay for Quads, 9: Packing of Parallelograms)
        :param interactive: Enable an interactive session using GMSH GUI.

        :type shp: ada.Shape
        :rtype: ada.FEM
        """
        gmsh.option.setNumber("Mesh.Algorithm", mesh_algo)
        gmsh.option.setNumber("Mesh.ElementOrder", order)
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)
        gmsh.option.setNumber("Mesh.Smoothing", 3)
        gmsh.option.setNumber("Geometry.Tolerance", 1e-3)

        gmsh.model.add(shp.name)
        shp.to_stp("temp_occ_in", os.path.join(self.work_dir))
        gmsh.open("gmsh/temp_occ_in.stp")

        model = gmsh.model
        factory = model.geo

        factory.synchronize()

        model.mesh.setRecombine(2, 1)
        model.mesh.generate(max_dim)
        model.mesh.removeDuplicateNodes()

        if interactive:
            gmsh.fltk.run()

    def _create_plate_geom(self, pl, beams, size=0.1, interactive=False):
        """

        :param pl:
        :param beams:
        :param size:
        :type pl: ada.Plate
        """

        corners = [n for n in pl.poly.points3d]
        corners += [corners[0]]
        if clockwise:
            corners.reverse()

        def is_on_line(data):
            """
            Evaluate intersection point between two lines

            :param data:
            :return:
            """
            l, bm = data
            A, B = np.array(l[0]), np.array(l[1])
            AB = B - A
            C = bm.n1.p
            D = bm.n2.p
            CD = D - C

            if (vector_length(A - C) < 1e-5) is True and (vector_length(B - D) < 1e-5) is True:
                return None

            s, t = intersect_calc(A, C, AB, CD)
            AB_ = A + s * AB
            CD_ = C + t * CD
            if (vector_length(AB_ - CD_) < 1e-4) is True and s not in (0.0, 1.0):
                return list(AB_), bm
            else:
                return None

        # Evalute Corner Points
        all_points = []
        crossing_beams = []
        for s, e in zip(corners[:-1], corners[1:]):
            li = (s, e)
            all_points.append(s)
            res = [x for x in map(is_on_line, [(li, bm) for bm in beams]) if x is not None]
            add_points = [r[0] for r in res]
            crossing_beams += filter(lambda x: x not in crossing_beams, [r[1] for r in res])
            sorted_li = list(sorted(add_points, key=lambda x: vector_length(np.array(x) - np.array(s))))
            all_points += sorted_li
            all_points.append(e)

        cp = []
        for i in all_points:
            cp += [gmsh.model.geo.addPoint(*i, size)]

        lines = []
        for c1, c2 in zip(cp[:-1], cp[1:]):
            lines.append(gmsh.model.geo.addLine(c1, c2))

        gmsh.model.geo.removeAllDuplicates()
        d = gmsh.model.geo.addCurveLoop(lines)
        surf = gmsh.model.geo.addPlaneSurface([d])

        gmsh.model.geo.synchronize()

        intersec_geom = []
        for bm in crossing_beams:
            p1, p2 = bm.n1.p, bm.n2.p
            s = self.get_point(p1)
            e = self.get_point(p2)

            if len(e) == 0 or len(s) == 0:
                continue
                # raise ValueError("Point not found")
            intersec_geom += [gmsh.model.geo.addLine(s[0][1], e[0][1])]

        gmsh.model.geo.synchronize()
        gmsh.model.mesh.embed(1, intersec_geom, 2, surf)

        if interactive:
            gmsh.fltk.run()

        self._pl_map[surf] = pl
        self._bm_map.update({i: j for i, j in zip(intersec_geom, crossing_beams)})

    def _create_bm_geom(self, bm, size):
        """

        :param bm:
        :type bm: ada.Beam
        """
        p1, p2 = bm.n1.p, bm.n2.p
        s = self.get_point(p1)
        e = self.get_point(p2)
        if len(s) == 0:
            s = [(0, gmsh.model.geo.addPoint(*p1.tolist(), size))]
        if len(e) == 0:
            e = [(0, gmsh.model.geo.addPoint(*p2.tolist(), size))]

        line = gmsh.model.geo.addLine(s[0][1], e[0][1])
        if line in self._bm_map.keys():
            raise ValueError("This should not happen!")
        self._bm_map[line] = bm

    def get_beam_elements(self, li, order):
        """

        :param li:
        :param order:
        :return:
        """

        model = gmsh.model
        bm = self._bm_map[li]
        segments = model.mesh.getElements(1, li)[1][0]
        fem_nodes = model.mesh.getElements(1, li)[2][0]
        elem_types, elem_tags, elem_node_tags = gmsh.model.mesh.getElements(1, li)
        face, dim, morder, numv, parv, _ = gmsh.model.mesh.getElementProperties(elem_types[0])

        def make_elem(j):
            no = []
            for i in range(numv):
                p1 = fem_nodes[numv * j + i]
                p1_co = gmsh.model.mesh.getNode(p1)[0]
                no.append(Node(p1_co, p1))
            if len(no) == 3:
                myorder = [0, 2, 1]
                no = [no[i] for i in myorder]
            bm_el_type = "B31" if order == 1 else "B32"

            return Elem(segments[j], no, bm_el_type, parent=self._part.fem)

        elements = list(map(make_elem, range(len(segments))))

        femset = FemSet(f"el{bm.name}_set", elements, "elset", parent=self._part.fem)
        self._part.fem.sets.add(femset)
        self._part.fem.add_section(
            FemSection(
                f"d{bm.name}_sec",
                "beam",
                femset,
                bm.material,
                bm.section,
                bm.ori[2],
                metadata=dict(beam=bm, numel=len(elements)),
            )
        )
        return elements

    def get_shell_elements(self, sh, order):
        """

        :param sh:
        :param order:
        :return:
        """

        pl = self._pl_map[sh]
        assert isinstance(pl, Plate)
        try:
            get_elems = gmsh.model.mesh.getElements(2, sh)
            segments = get_elems[1][0]
        except BaseException as e:
            logging.debug(e)
            return []
        fem_nodes = gmsh.model.mesh.getElements(2, sh)[2][0]
        elemTypes, elemTags, elemNodeTags = gmsh.model.mesh.getElements(2, sh)
        face, dim, morder, numv, parv, _ = gmsh.model.mesh.getElementProperties(elemTypes[0])

        elem_type = gmsh_map[face]

        def make_elem(j):
            el_id = segments[j]
            nonlocal elem_type
            nonlocal numv
            nonlocal fem_nodes
            no = []
            for i in range(numv):
                p1 = fem_nodes[numv * j + i]
                p1_co = gmsh.model.mesh.getNode(p1)[0]
                no.append(Node(p1_co, p1))

            return Elem(el_id, no, elem_type, parent=self._part.fem)

        elements = list(map(make_elem, range(len(segments))))

        femset = FemSet(f"el{pl.name}_set", elements, "elset")
        self._part.fem.add_set(femset)
        self._part.fem.add_section(
            FemSection(
                f"sh{pl.name}_sec",
                "shell",
                femset,
                pl.material,
                local_z=pl.n,
                thickness=pl.t,
                int_points=5,
                metadata=dict(beam=pl, numel=len(elements)),
            )
        )
        return elements

    def get_point(self, p):
        tol = self._tol
        tol_vec = np.array([tol, tol, tol])
        lower = np.array(p) - tol_vec
        upper = np.array(p) + tol_vec
        return gmsh.model.getEntitiesInBoundingBox(*lower.tolist(), *upper.tolist(), 0)

    def get_mesh_nodes(self, e):
        """

        :param e:
        :return:
        """

        nco = gmsh.model.mesh.getNode(e)
        return Node([roundoff(x) for x in nco[0]], e, parent=self._part.fem)

    @property
    def work_dir(self):
        return self._work_dir


def get_point(gmsh_session, p, tol=1e-5):
    tol_vec = np.array([tol, tol, tol])
    lower = np.array(p) - tol_vec
    upper = np.array(p) + tol_vec
    return gmsh_session.model.getEntitiesInBoundingBox(*lower.tolist(), *upper.tolist(), 0)


def get_nodes_and_elements(gmsh_session, fem, fem_set_name="all_elements"):
    nodes = list(gmsh_session.model.mesh.getNodes(-1, -1))
    # Get nodes
    fem._nodes = Nodes(
        [
            Node(
                [roundoff(x) for x in gmsh_session.model.mesh.getNode(n)[0]],
                n,
                parent=fem,
            )
            for n in nodes[0]
        ],
        parent=fem,
    )

    # Get elements
    elemTypes, elemTags, elemNodeTags = gmsh_session.model.mesh.getElements(2, -1)
    elements = []
    for k, element_list in enumerate(elemTags):
        face, dim, morder, numv, parv, _ = gmsh_session.model.mesh.getElementProperties(elemTypes[k])
        elem_type = gmsh_map[face]
        for j, eltag in enumerate(element_list):
            nodes = []
            for i in range(numv):
                idtag = numv * j + i
                p1 = elemNodeTags[k][idtag]
                nodes.append(fem.nodes.from_id(p1))

            el = Elem(eltag, nodes, elem_type, parent=fem)
            elements.append(el)
    fem._elements = FemElements(elements, fem_obj=fem)
    femset = FemSet(fem_set_name, elements, "elset")
    fem.sets.add(femset)


def eval_thick_normal_from_cog_of_beam_plate(beam, cog):
    """

    :param beam:
    :param cog:
    :type beam: ada.Beam
    :return:
    """
    from ada.core.utils import vector_length
    from ada.sections import SectionCat

    if SectionCat.is_circular_profile(beam.section.type):
        tol = beam.section.r / 8
    else:
        tol = beam.section.h / 8
    t, n, c = None, None, None
    xdir, ydir, zdir = beam.ori

    n1 = beam.n1.p
    n2 = beam.n2.p
    h = beam.section.h
    w_btn = beam.section.w_btn
    w_top = beam.section.w_top

    if beam.section.type in SectionCat.iprofiles + SectionCat.igirders:
        p11 = n1 + zdir * h / 2
        p12 = p11 + ydir * w_top / 2
        p21 = n2 + zdir * h / 2
        p22 = p21 + ydir * w_top / 2

        p11btn = n1 - zdir * h / 2 + ydir * w_btn / 2
        p12btn = p11btn - ydir * w_top / 2
        p21btn = n2 - zdir * h / 2
        p22btn = p21btn + ydir * w_btn / 2

        web = (n1 + n2) / 2

        fl_top_right = (p11 + p12 + p21 + p22) / 4
        fl_top_left = fl_top_right - ydir * w_top / 2
        fl_btn_right = (p11btn + p12btn + p21btn + p22btn) / 4
        fl_btn_left = fl_btn_right - ydir * w_btn / 2

        if vector_length(web - cog) < tol:
            t, n, c = beam.section.t_w, ydir, "web"

        for x in [fl_top_right, fl_top_left]:
            if vector_length(x - cog) < tol:
                t, n, c = beam.section.t_ftop, zdir, "top_fl"

        for x in [fl_btn_right, fl_btn_left]:
            if vector_length(x - cog) < tol:
                t, n, c = beam.section.t_fbtn, zdir, "btn_fl"

        if t is None:
            raise ValueError("The thickness is not valid")

    return t, n, c


def _init_gmsh_session():
    gmsh_session = gmsh
    try:
        gmsh_session.finalize()
    except BaseException as e:
        logging.debug(e)
    gmsh_session.initialize()
    gmsh_session.option.setNumber("General.Terminal", 1)
    return gmsh_session
