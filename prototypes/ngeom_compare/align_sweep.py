"""Python reference sweep: profile-along-directrix shell mesh for the alignment fixture.

Builds on align_eval.directrix_points by adding per-station fixed-reference frames and sweeping the
(baked IfcDerivedProfileDef) 2D profile -> a closed triangle shell. Validated vs the ifcopenshell
body bbox (align_oracle: x[-0.001,885.72] y[-213.95,5] z[148.515,150.856]).

Spec for the adacpp C++ ng:: sweep. No OCC.

Run: pixi run -e tests-adacpp python prototypes/ngeom_compare/align_sweep.py
"""

from __future__ import annotations

import align_eval as ae
import numpy as np

# IfcDerivedProfileDef #114: parent #18 outline points, baked by operator #115 (Axis1=(0,-1)) ->
# (x,y) -> (y,-x). Hand-applied here (matches piece-1a derived_profile_def).
_PARENT = np.array([(-4.0, 0.0), (-5.0, -1.0), (5.0, -1.0), (4.0, 0.0)])
PROFILE = np.column_stack([_PARENT[:, 1], -_PARENT[:, 0]])  # (y, -x)

FIXED_REF = np.array([0.0, 0.0, 1.0])


def _frames(pts):
    """Per-station 3D tangent + fixed-reference in-plane axes (up, lateral)."""
    T = np.gradient(pts, axis=0)
    T /= np.linalg.norm(T, axis=1, keepdims=True)
    F = FIXED_REF
    lateral = np.cross(T, F)
    lateral /= np.linalg.norm(lateral, axis=1, keepdims=True)
    up = np.cross(lateral, T)
    up /= np.linalg.norm(up, axis=1, keepdims=True)
    return T, up, lateral


def sweep(n_per=600):
    pts = ae.directrix_points(n_per=n_per)
    T, up, lateral = _frames(pts)
    M = len(PROFILE)
    N = len(pts)
    # rings[i] = N stations x M profile verts. profile-x -> up, profile-y -> lateral.
    rings = (
        pts[:, None, :] + PROFILE[None, :, 0, None] * up[:, None, :] + PROFILE[None, :, 1, None] * lateral[:, None, :]
    )  # (N, M, 3)
    verts = rings.reshape(-1, 3)

    faces = []
    for i in range(N - 1):
        for j in range(M):
            a = i * M + j
            b = i * M + (j + 1) % M
            c = (i + 1) * M + j
            d = (i + 1) * M + (j + 1) % M
            faces.append((a, b, d))
            faces.append((a, d, c))
    # end caps (profile is a convex-ish quad -> fan triangulation)
    for j in range(1, M - 1):
        faces.append((0, j, j + 1))  # start cap
        base = (N - 1) * M
        faces.append((base, base + j + 1, base + j))  # end cap
    return verts, np.array(faces)


def main():
    verts, faces = sweep(n_per=600)
    print(f"sweep verts {len(verts)}  tris {len(faces)}")
    print(f"bbox min {verts.min(0)}")
    print(f"bbox max {verts.max(0)}")
    print("oracle body  min [-9.99e-04 -2.139e+02  1.485e+02]  max [885.72 5. 150.856]")


if __name__ == "__main__":
    main()
