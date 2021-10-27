from ada.ifc.utils import ifc_p


def create_reference_subrep(f, global_axes):
    model_rep = f.create_entity("IfcGeometricRepresentationContext", None, "Model", 3, 1.0e-05, global_axes, None)
    body_sub_rep = f.create_entity(
        "IfcGeometricRepresentationSubContext",
        "Body",
        "Model",
        None,
        None,
        None,
        None,
        model_rep,
        None,
        "MODEL_VIEW",
        None,
    )
    ref_sub_rep = f.create_entity(
        "IfcGeometricRepresentationSubContext",
        "Reference",
        "Model",
        None,
        None,
        None,
        None,
        model_rep,
        None,
        "GRAPH_VIEW",
        None,
    )

    return {"model": model_rep, "body": body_sub_rep, "reference": ref_sub_rep}


def ifc_vertex(p, f):
    return f.create_entity("IfcVertexPoint", ifc_p(f, p))
