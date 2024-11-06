import ada
import ada.cadit.sat.write.writer as se_writer
import numpy as np
from ada.cadit.sat.write import sat_entities as se
from ada.base.types import GeomRepr


class IDGenerator:
    def __init__(self, start_id: int = 1):
        self.current_id = start_id

    def next_id(self) -> int:
        id_val = self.current_id
        self.current_id += 1
        return id_val


def plate_to_sat_body(pl: ada.Plate, start_id: int, geo_repr: GeomRepr) -> str:
    """Convert a Plate object to a SAT body string"""
    id_gen = IDGenerator(start_id)
    if geo_repr != GeomRepr.SHELL:
        raise ValueError(f"Unsupported geometry representation: {geo_repr}")

    pmin = np.min(pl.poly.points2d, axis=0)
    pmax = np.max(pl.poly.points2d, axis=0)
    bbox2d = [pmin[0], pmin[1], 0, pmax[0], pmax[1], 0]

    # Initialize strings for each component
    # Create main entities using ID generator
    lump_id = id_gen.next_id()
    shell_id = id_gen.next_id()
    face_id = id_gen.next_id()
    loop_id = id_gen.next_id()

    body = se.Body(id_gen.next_id(), lump_id, bbox2d)
    lump = se.Lump(lump_id, shell_id, bbox2d)
    shell = se.Shell(shell_id, face_id, bbox2d)
    loop = se.Loop(loop_id, id_gen.next_id(), bbox2d)

    string_attrib_name = se.StringAttribName(id_gen.next_id(), "SESAM", face_id)
    surface = se.PlaneSurface(id_gen.next_id())

    face = se.Face(face_id, loop_id, lump_id, string_attrib_name.entity_id, surface.entity_id)


    vertices = []
    points = []
    edges = []
    coedges = []
    # Create Edge Strings

    shell_geom = pl.shell_geom()
    for i, edge in enumerate(shell_geom.geometry.outer_boundary.segments):
        edge_id = id_gen.next_id()
        if i == 0:
            coedge_id = loop.coedge_id
        else:
            coedge_id = id_gen.next_id()

        # start
        start_pt = edge.start
        point_start_id = id_gen.next_id()
        vertex_start_id = id_gen.next_id()

        edge = se.Edge(edge_id, )
        coedge = se.CoEdge(coedge_id, edge_id, loop_id, 1)
        coedges.append(coedge)



    # Create Point and Vertex Strings
    for node in pl.nodes:
        point_id = id_gen.next_id()
        vertex_start_id = id_gen.next_id()
        point_str += se_writer.create_point_string(point_id, node.p)
        vertex_string += se_writer.create_vertex_string(
            vertex_start_id,
        )

    # Create Coedge Strings
    for coedge in pl.coedges:
        coedge_id = id_gen.next_id()
        coedge_string += create_coedge_string(coedge_id, edge_id + coedge.edge, loop_id, coedge.orientation)

        # Build full ACIS string
    sat_data = (
        [
            body.to_string(),
            lump.to_string(),
            shell.to_string(),
            face.to_string(),
            loop.to_string(),
        ]
        + [v.to_string() for v in vertices]
        + [p.to_string() for p in points]
    )

    return "\n".join(sat_data)
