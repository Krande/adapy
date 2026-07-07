"""Native IfcFaceBasedSurfaceModel reader (a set of IfcConnectedFaceSet, each a set of IfcFace).

Previously fell back to the OCC kernel. The reader now builds the native face-set hierarchy
(FaceBasedSurfaceModel -> ConnectedFaceSet -> Face -> FaceBound -> PolyLoop), which the shared
OCC/NGEOM face-set builders already tessellate. The product's ObjectPlacement is applied via the
face-set placement baker (transforms the loop vertices), so it lands in world coordinates.
"""

from __future__ import annotations

import numpy as np

import ada
from ada.config import Config


def test_face_based_surface_model_native_and_placed(example_files):
    """surface-model.ifc: an IfcFaceBasedSurfaceModel reads natively (no OCC kernel) and, with its
    +1 m x ObjectPlacement applied, matches the ifcopenshell oracle bbox ([0.5,-0.5,0]..[1.5,0.5,2])."""
    import ada.geom.surfaces as su

    Config().update_config_globally("ifc_import_shape_geom", True)
    a = ada.from_ifc(example_files / "ifc_files/surface-model.ifc")
    objs = list(a.get_all_physical_objects())
    assert len(objs) == 1
    o = objs[0]
    assert isinstance(o.geom.geometry, su.FaceBasedSurfaceModel)
    assert o._occ_cache is None  # not built via the IfcOpenShell kernel

    from ada.occ.tessellating import BatchTessellator

    bt = BatchTessellator()
    pts = [np.asarray(ms.position, float).reshape(-1, 3) for ms in bt.batch_tessellate(objs)]
    p = np.vstack([x for x in pts if len(x)])
    assert len(p) > 0
    assert np.allclose(p.min(0), (0.5, -0.5, 0.0), atol=0.02), p.min(0)
    assert np.allclose(p.max(0), (1.5, 0.5, 2.0), atol=0.02), p.max(0)
