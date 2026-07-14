"""Diagnose the z divergence between our analytic directrix and ifcopenshell's IfcGradientCurve.

Extracts the ORDERED 3D directrix polyline from ifcopenshell (#79) by walking geometry.edges,
computes cumulative xy-distance s along it, and compares z(s) against our analytic eval. Also
tessellates each gradient CurveSegment (#80/#88/#96) and base CurveSegment (#55/#63/#71)
individually to see how ifcopenshell places/orders them.

Run: pixi run -e tests-adacpp python prototypes/ngeom_compare/align_zdiff.py
"""

from __future__ import annotations

import align_eval as ae
import ifcopenshell
import ifcopenshell.geom
import numpy as np

FIXTURE = "files/ifc_files/fixed-reference-swept-area-solid.ifc"


def _settings():
    s = ifcopenshell.geom.settings()
    s.set(s.USE_WORLD_COORDS, True)
    return s


def _ordered_polyline(verts, edges):
    """Walk an edge list (flat pairs) into an ordered vertex-index path from a degree-1 end."""
    from collections import defaultdict

    adj = defaultdict(list)
    e = np.asarray(edges, dtype=int).reshape(-1, 2)
    for a, b in e:
        adj[a].append(b)
        adj[b].append(a)
    ends = [v for v, nb in adj.items() if len(nb) == 1]
    start = ends[0] if ends else int(e[0, 0])
    path = [start]
    prev = None
    cur = start
    while True:
        nxts = [n for n in adj[cur] if n != prev]
        if not nxts:
            break
        prev, cur = cur, nxts[0]
        path.append(cur)
        if cur == start or len(path) > len(verts) + 5:
            break
    return verts[np.array(path)]


def main():
    f = ifcopenshell.open(FIXTURE)
    s = _settings()

    # ordered 3D directrix from #79
    sh = ifcopenshell.geom.create_shape(s, f.by_id(79))
    v = np.asarray(sh.verts, float).reshape(-1, 3)
    ed = np.asarray(sh.edges, int)
    poly = _ordered_polyline(v, ed)
    # cumulative xy distance along the ordered polyline
    dxy = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(poly[:, :2], axis=0), axis=1))]
    print(f"#79 ordered polyline: {len(poly)} pts, total xy-len {dxy[-1]:.3f}")

    # our analytic z at the same cumulative distance
    z_ours = []
    V = ae._sample_polyline(ae.vertical_segs(), 2000)
    order = np.argsort(V[:, 0])
    z_ours = np.interp(dxy, V[order, 0], V[order, 1])

    print("\n s(xy)     x        y      z_ifc    z_ours   dz")
    for k in np.linspace(0, len(poly) - 1, 12).astype(int):
        print(
            f"{dxy[k]:7.1f} {poly[k,0]:8.1f} {poly[k,1]:8.1f} {poly[k,2]:8.3f} {z_ours[k]:8.3f} {poly[k,2]-z_ours[k]:+7.3f}"
        )

    # where does dz cross a threshold? find first index where |dz|>0.01
    dz = poly[:, 2] - z_ours
    bad = np.where(np.abs(dz) > 0.01)[0]
    if len(bad):
        i = bad[0]
        print(
            f"\nfirst divergence at idx {i}: s_xy={dxy[i]:.2f} (x={poly[i,0]:.1f},y={poly[i,1]:.1f}) z_ifc={poly[i,2]:.3f} z_ours={z_ours[i]:.3f}"
        )

    # segment-3 horizontal starts at distance 550 (x~550), the circle. Print z_ifc vs the
    # vertical-line z that SHOULD apply there (149.522 + 0.000444*(s-550)).
    print("\nhorizontal-circle region (s>=550): z_ifc vs expected straight-grade 149.522+4.444e-4*(s-550)")
    for k in range(len(poly)):
        if dxy[k] >= 550 and k % max(1, len(poly) // 30) == 0:
            exp = 149.522222225005 + 4.44444400554072e-4 * (dxy[k] - 550)
            print(f"  s={dxy[k]:7.1f} z_ifc={poly[k,2]:.4f} z_line={exp:.4f} d={poly[k,2]-exp:+.4f}")


if __name__ == "__main__":
    main()
