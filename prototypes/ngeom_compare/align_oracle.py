"""Oracle for the IFC4x3 alignment fixed-reference swept-area solid.

Tessellate files/ifc_files/fixed-reference-swept-area-solid.ifc with ifcopenshell.geom
(USE_WORLD_COORDS) and report bbox / tri / vertex counts for the AdvancedSweptSolid body
(#113). This is the parity reference the native ngeom evaluator must match.

Run: pixi run -e tests-adacpp python prototypes/ngeom_compare/align_oracle.py
"""

from __future__ import annotations

import ifcopenshell
import ifcopenshell.geom
import numpy as np

FIXTURE = "files/ifc_files/fixed-reference-swept-area-solid.ifc"


def main() -> None:
    f = ifcopenshell.open(FIXTURE)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)

    # The swept solid is the body of the IfcBuiltElement #107 (SimpleProfile).
    elem = f.by_id(107)
    shape = ifcopenshell.geom.create_shape(settings, elem)
    geo = shape.geometry
    verts = np.asarray(geo.verts, dtype=float).reshape(-1, 3)
    faces = np.asarray(geo.faces, dtype=int).reshape(-1, 3)
    print("oracle body (#107 SimpleProfile)")
    print(f"  verts: {len(verts)}  tris: {len(faces)}")
    print(f"  bbox min: {verts.min(axis=0)}")
    print(f"  bbox max: {verts.max(axis=0)}")

    # Sample the directrix (IfcGradientCurve #79) via the Curve3D 'Axis' rep so we have a
    # ground-truth (x,y,z)(s) to validate the analytic evaluator against.
    # (IfcShapeRepresentation #106 'Axis' Curve3D contains #79)
    csettings = ifcopenshell.geom.settings()
    csettings.set(csettings.USE_WORLD_COORDS, True)
    try:
        cshape = ifcopenshell.geom.create_shape(csettings, f.by_id(79))
        cverts = np.asarray(cshape.verts, dtype=float).reshape(-1, 3)
        print("\noracle directrix (#79 IfcGradientCurve)")
        print(f"  sample pts: {len(cverts)}")
        print(f"  first: {cverts[0]}")
        print(f"  last:  {cverts[-1]}")
        print(f"  bbox min: {cverts.min(axis=0)}  max: {cverts.max(axis=0)}")
        np.save(
            "/tmp/claude-1000/-home-kristoffer-code-dap/d103bfc1-7e2e-4f7e-ba7c-8e213d35e3df/scratchpad/directrix_oracle.npy",
            cverts,
        )
    except Exception as e:  # noqa: BLE001
        print(f"directrix sample failed: {e!r}")


if __name__ == "__main__":
    main()
