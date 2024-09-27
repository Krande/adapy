from ada.cadit.ifc.write.geom.points import cpt


def ifc_vertex(p, f):
    return f.create_entity("IfcVertexPoint", cpt(f, p))
