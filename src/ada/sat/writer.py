def create_face_string(vertices):
    face_string = "facet normal 0.0 0.0 0.0\nouter loop\n"
    for vertex in vertices:
        face_string += "vertex {} {} {}\n".format(vertex[0], vertex[1], vertex[2])
    face_string += "endloop\nendfacet\n"
    return face_string


def create_sat_string(vertices):
    sat_string = "solid\n"
    sat_string += "HEADER\n"
    sat_string += "SAT\n"
    sat_string += "ASCII\n"
    sat_string += "2\n"
    sat_string += "200\n"
    sat_string += "ENDSEC\n"
    sat_string += create_face_string(vertices)
    sat_string += "endsolid"
    return sat_string
