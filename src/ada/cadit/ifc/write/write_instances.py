from typing import TYPE_CHECKING

from ada.core.guid import create_guid
from ada.core.utils import to_real

from ..utils import create_ifc_placement, ifc_dir
from .geom.points import cpt

if TYPE_CHECKING:
    from ifcopenshell import file

    from ada.api.transforms import Instance


def write_mapped_instance(instance: "Instance", f: "file"):
    elem = instance.instance_ref
    products = list(filter(lambda x: x.GlobalId == elem.guid, f.by_type("IfcProduct")))
    if len(products) != 1:
        raise ValueError(f'Unable to find IFC element with guid="{elem.guid}"')
    ifc_elem = products[0]
    origin = create_ifc_placement(f)  # , loc_z=elem.xvec.astype(float).tolist()
    body = ifc_elem.Representation.Representations[0]

    rep_map = f.create_entity("IFCREPRESENTATIONMAP", origin, body)
    mapped_instances = []
    for place in instance.placements:
        tra = f.create_entity(
            "IFCCARTESIANTRANSFORMATIONOPERATOR3DNONUNIFORM",
            Axis1=ifc_dir(f, place.xdir),
            Axis2=ifc_dir(f, place.ydir),
            LocalOrigin=cpt(f, place.origin),
            Scale=to_real(place.scale),
            Axis3=ifc_dir(f, place.zdir),
            Scale2=to_real(place.scale),
            Scale3=to_real(place.scale),
        )

        mapped_item = f.create_entity("IFCMAPPEDITEM", rep_map, tra)
        mapped_instances.append(mapped_item)

    shape_rep = f.create_entity(
        "IFCSHAPEREPRESENTATION", body.ContextOfItems, "body", "MappedRepresentation", mapped_instances
    )

    prod_def_shape = f.create_entity(
        "IFCPRODUCTDEFINITIONSHAPE", Name=None, Description=None, Representations=[shape_rep]
    )

    f.create_entity(
        "IFCBUILDINGELEMENTPROXY",
        create_guid(),
        None,
        elem.name + "_instances",
        "Mapped Instances",
        None,
        prod_def_shape,
        None,
        None,
    )

    # 32= IFCBUILDINGELEMENTPROXY('1kTvXnbbzCWw8lcMd1dR4o',$,'P-1','sample proxy',$,#44,#24,$,$);
