from ada.ifc.utils import ifc_p


def ifc_vertex(p, f):
    return f.create_entity("IfcVertexPoint", ifc_p(f, p))
