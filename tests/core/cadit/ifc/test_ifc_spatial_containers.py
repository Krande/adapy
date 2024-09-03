import ada
from ada.base.ifc_types import SpatialTypes


def test_all_spatial_containers():
    for spatial_class in SpatialTypes:
        (ada.Assembly() / ada.Part(f"Spatial{spatial_class}", ifc_class=spatial_class)).to_ifc(
            destination=f"temp/{spatial_class}.ifc", file_obj_only=False, validate=True
        )
