def create_vertex_string(vertex_id, vertex):
    vertex_string = f"{vertex_id} vertex {vertex[0]} {vertex[1]} {vertex[2]} #\n"
    return vertex_string


def create_edge_string(edge_id, vertex_start_id, vertex_end_id, vertex):
    edge_string = (
        f"{edge_id} edge -1 -1 -1 -1 {vertex_start_id} 0 {vertex_end_id} 0.51249999999999996 "
        f"{vertex_start_id} {vertex_end_id} forward @7 unknown T"
        f" {vertex[0]} {vertex[1]} {vertex[2]} {vertex[0]} {vertex[1]} {vertex[2]} #\n"
    )
    return edge_string


def create_coedge_string(coedge_id, edge_id, loop_id, orientation):
    coedge_string = f"{coedge_id} coedge -1 -1 -1 -1 {edge_id} {edge_id} -1 {loop_id} {orientation} #\n"
    return coedge_string


def create_loop_string(loop_id, coedge_id):
    loop_string = (
        f"{loop_id} loop -1 -1 -1 -1 -1 {coedge_id} -1 T -0.375 "
        f"-0.28749999999999998 0 0.4375 0.22500000000000001 0 periphery #\n"
    )
    return loop_string


def create_face_string(face_id, loop_id):
    face_string = (
        f"{face_id} face -1 -1 -1 -1 -1 {loop_id} -1 -1 forward double out T "
        f"-0.375 -0.28749999999999998 0 0.4375 0.22500000000000001 0 F #\n"
    )
    return face_string


def create_shell_string(shell_id, face_id):
    shell_string = (
        f"{shell_id} shell -1 -1 -1 -1 -1 -1 {face_id} -1 T "
        f"-0.375 -0.28749999999999998 0 0.4375 0.22500000000000001 0 #\n"
    )
    return shell_string


def create_lump_string(lump_id, shell_id):
    lump_string = (
        f"{lump_id} lump -1 -1 -1 -1 -1 {shell_id} -1 T "
        f"-0.375 -0.28749999999999998 0 0.4375 0.22500000000000001 0 #\n"
    )
    return lump_string


def create_body_string(body_id, lump_id):
    body_string = (
        f"{body_id} body -1 {lump_id} -1 -1 -1 -1 T " f"-0.375 -0.28749999999999998 0 0.4375 0.22500000000000001 0 #\n"
    )
    return body_string
