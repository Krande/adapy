from ada.config import get_logger

from .exceptions import InsufficientPointsError

logger = get_logger()


def get_plates_from_satd(satd: dict) -> dict:
    plate_geom = dict()

    for face in filter(lambda x: x[0] == "face", satd.values()):
        try:
            res = get_face_from_satd(face, satd)
        except KeyError as e:
            logger.warning(f"Unable to import face due to KeyError -> {e}")
            continue
        except InsufficientPointsError as e:
            logger.warning(f"Unable to import face due to Insufficient points -> {e}")
            continue
        plate_geom.update(res)

    return plate_geom


def get_face_from_satd(face: tuple, satd: dict):
    # Face row
    name_idx = 1
    loop_idx = 6

    # Loop row
    coedge_ref = 6

    face_ref = get_value_from_satd(face[name_idx], satd)[-1]

    loop = get_value_from_satd(face[loop_idx], satd)
    coedge_start_id = loop[coedge_ref]
    coedge_first = get_value_from_satd(coedge_start_id, satd)

    coedge_first_direction = str(coedge_first[-3])

    # Coedge row
    next_coedge_idx = 5 if coedge_first_direction == "forward" else 6

    next_coedge = True
    coedge_next_id = coedge_first[next_coedge_idx]
    edges = [coedge_first]

    max_iter = 500
    i = 0
    while next_coedge is True:
        coedge = get_value_from_satd(coedge_next_id, satd)
        edges.append(coedge)

        coedge_next_id = coedge[next_coedge_idx]
        if coedge_next_id == coedge_start_id:
            next_coedge = False

        i += 1
        if i > max_iter:
            raise ValueError(f"Found {i} points which is over max={max_iter}")

    p1, p2 = get_points_from_edge(coedge_first, satd)

    points = [p1, p2]

    for coedge in edges:
        p1, p2 = get_points_from_edge(coedge, satd)
        edge_direction = str(coedge[-3])
        if edge_direction == "forward":
            p = p2
        else:
            p = p1
        if p not in points:
            points.append(p)

    if len(points) < 3:
        raise InsufficientPointsError("Plates cannot have < 3 points")

    if coedge_first_direction == "reversed":
        points.reverse()

    return {face_ref: points}


def get_points_from_edge(coedge: tuple, satd: dict):
    # Coedge row
    edge_ref = 8

    # Edge row
    vert1_idx = 5
    vert2_idx = 7
    # edge_type_idx = 8

    # Vertex row
    p_idx = -1

    edge = get_value_from_satd(coedge[edge_ref], satd)
    vert1 = get_value_from_satd(edge[vert1_idx], satd)
    vert2 = get_value_from_satd(edge[vert2_idx], satd)
    # edge_type = get_value_from_satd(edge[edge_type_idx], satd)
    p1 = get_value_from_satd(vert1[p_idx], satd)
    p2 = get_value_from_satd(vert2[p_idx], satd)
    n1 = tuple([float(x) for x in p1[-3:]])
    n2 = tuple([float(x) for x in p2[-3:]])
    return n1, n2


def get_value_from_satd(val_str: str, satd: dict) -> tuple:
    return satd[val_str.replace("$", "")]
