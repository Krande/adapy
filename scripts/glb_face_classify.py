"""Per-face classification of subject vs reference GLB.

For each *named* mesh primitive in the subject GLB (one per BREP face
when produced by adapy with merge_meshes=False), compute:

  * centroid_xml          — area-weighted triangle centroid
  * normal_xml            — area-weighted triangle normal (unit, sign-agnostic)
  * area_xml              — total triangle area
  * step_match            — closest STEP triangle (by centroid) with
                            *aligned* normal (|dot| >= cos(30°))
  * centroid_offset       — distance from face centroid to that STEP centroid
  * normal_angle_deg      — angle between face normal and that STEP normal
  * plane_offset          — signed distance from face centroid to STEP local plane
                            (= residual after rotation / "out of plane" amount)

then bucket each face into:

  OK              – everything within tol
  POS_BAD_ROT_OK  – centroid far but normal aligned (face translated)
  POS_OK_ROT_BAD  – centroid close but normal misaligned (face rotated /
                    flipped on the same hinge — typical SAT pcurve sign bug)
  WHOLLY_BAD      – centroid far AND normal misaligned (or no nearby STEP
                    geometry at all)

Buckets are exported as separate GLBs so the patterns can be inspected
in the viewer alongside the originals.
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

import numpy as np
import trimesh


# ─────────────────── geometry helpers ───────────────────


def triangles(mesh: trimesh.Trimesh) -> np.ndarray:
    return mesh.vertices[mesh.faces] if mesh.faces.shape[0] else np.empty((0, 3, 3))


def tri_centroids(tris: np.ndarray) -> np.ndarray:
    return tris.mean(axis=1) if tris.shape[0] else np.empty((0, 3))


def tri_normals_areas(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if tris.shape[0] == 0:
        return np.empty((0, 3)), np.empty((0,))
    a = tris[:, 1] - tris[:, 0]
    b = tris[:, 2] - tris[:, 0]
    cross = np.cross(a, b)
    area2 = np.linalg.norm(cross, axis=1)  # = 2 × triangle area
    nrm = np.where(area2[:, None] > 0, cross / np.where(area2[:, None] == 0, 1, area2[:, None]), 0)
    return nrm, 0.5 * area2


def face_centroid_normal_area(geom: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray, float]:
    tris = triangles(geom)
    if tris.shape[0] == 0:
        return np.zeros(3), np.zeros(3), 0.0
    cs = tri_centroids(tris)
    ns, areas = tri_normals_areas(tris)
    total_area = float(areas.sum())
    if total_area <= 0:
        return cs.mean(axis=0), ns.mean(axis=0), 0.0
    centroid = (cs * areas[:, None]).sum(axis=0) / total_area
    # Sign-agnostic average normal: align all triangle normals with
    # the first one before averaging so a face wound in mixed
    # directions doesn't average to zero.
    ref = ns[0]
    flip = np.sign(ns @ ref)
    flip[flip == 0] = 1
    aligned = ns * flip[:, None]
    n_avg = (aligned * areas[:, None]).sum(axis=0) / total_area
    n_norm = np.linalg.norm(n_avg)
    if n_norm > 0:
        n_avg = n_avg / n_norm
    return centroid, n_avg, total_area


# ─────────────────── grid hash ───────────────────


class GridHash:
    def __init__(self, points: np.ndarray, normals: np.ndarray, tol: float):
        self.tol = float(tol)
        self.cell = 4.0 * self.tol  # bigger cell so the 27-neighbour query
        # captures candidates within tol even for face-centroid queries
        # whose true nearest point might be a few cells away if normals
        # don't match in the closest cells.
        self.points = points
        self.normals = normals
        self.cells: dict[tuple[int, int, int], list[int]] = collections.defaultdict(list)
        idx = np.floor(points / self.cell).astype(np.int64)
        for i, (ix, iy, iz) in enumerate(idx):
            self.cells[(ix, iy, iz)].append(i)

    def query_aligned(
        self,
        q: np.ndarray,
        qn: np.ndarray,
        normal_min_dot: float,
        max_search_radius_cells: int = 1,
    ) -> tuple[float, float, int]:
        """Return (centroid_distance, normal_dot, ref_index) of the
        closest reference triangle whose normal aligns with ``qn``
        (|dot| >= normal_min_dot). Returns ``(inf, 0, -1)`` if none
        in the searched neighbourhood."""
        idx = np.floor(q / self.cell).astype(np.int64)
        rng = max_search_radius_cells
        offsets = []
        for dx in range(-rng, rng + 1):
            for dy in range(-rng, rng + 1):
                for dz in range(-rng, rng + 1):
                    offsets.append((dx, dy, dz))
        candidates: list[int] = []
        for dx, dy, dz in offsets:
            key = (int(idx[0] + dx), int(idx[1] + dy), int(idx[2] + dz))
            lst = self.cells.get(key)
            if lst is not None:
                candidates.extend(lst)
        if not candidates:
            return float("inf"), 0.0, -1
        cand = self.points[candidates]
        cand_n = self.normals[candidates]
        diff = cand - q
        d = np.linalg.norm(diff, axis=1)
        dot = np.abs(cand_n @ qn)
        ok = dot >= normal_min_dot
        if not ok.any():
            # no aligned candidate; return closest unaligned
            i_min = int(np.argmin(d))
            return float(d[i_min]), float(dot[i_min]), candidates[i_min]
        d_ok = np.where(ok, d, np.inf)
        i_min = int(np.argmin(d_ok))
        return float(d[i_min]), float(dot[i_min]), candidates[i_min]


# ─────────────────── classification ───────────────────


def classify(centroid_offset: float, normal_angle_deg: float,
             pos_tol: float, ang_tol: float) -> str:
    pos_ok = centroid_offset <= pos_tol
    rot_ok = normal_angle_deg <= ang_tol
    if pos_ok and rot_ok:
        return "OK"
    if pos_ok and not rot_ok:
        return "POS_OK_ROT_BAD"
    if not pos_ok and rot_ok:
        return "POS_BAD_ROT_OK"
    return "WHOLLY_BAD"


# ─────────────────── main ───────────────────


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("reference", type=pathlib.Path)
    ap.add_argument("subject", type=pathlib.Path)
    ap.add_argument("--pos-tol", type=float, default=10.0,
                    help="Centroid-distance threshold (mm) for POS_OK (default 10).")
    ap.add_argument("--ang-tol", type=float, default=10.0,
                    help="Normal-angle threshold (deg) for ROT_OK (default 10).")
    ap.add_argument("--ref-tol", type=float, default=5.0,
                    help="Reference-triangle search/cell tolerance (mm, default 5).")
    ap.add_argument("--out", type=pathlib.Path, default=None,
                    help="Optional dir to write per-bucket GLBs and a JSON report.")
    args = ap.parse_args()

    print(f"[load] reference: {args.reference}")
    ref_scene = trimesh.load(args.reference, force="scene")
    ref_concat = trimesh.util.concatenate(
        [g for g in ref_scene.geometry.values() if isinstance(g, trimesh.Trimesh)]
    )
    ref_tris = triangles(ref_concat)
    ref_centroids = tri_centroids(ref_tris)
    ref_normals, ref_areas = tri_normals_areas(ref_tris)
    print(f"  reference triangles: {ref_centroids.shape[0]}")

    print(f"[load] subject:   {args.subject}")
    sub_scene = trimesh.load(args.subject, force="scene")
    n_geoms = sum(1 for g in sub_scene.geometry.values() if isinstance(g, trimesh.Trimesh))
    print(f"  subject geom(s): {n_geoms}")
    if n_geoms < 50:
        sys.exit(
            "Subject has fewer than 50 named geoms — run with merge_meshes=False "
            "or this is an already-merged file. Per-face analysis won't be meaningful."
        )

    print(f"[build] reference grid hash (cell={4 * args.ref_tol} mm)")
    grid = GridHash(ref_centroids, ref_normals, args.ref_tol)
    normal_min_dot = float(np.cos(np.deg2rad(args.ang_tol)))

    print(f"[query] {n_geoms} subject faces vs reference  "
          f"(pos_tol={args.pos_tol} mm, ang_tol={args.ang_tol}°)")

    rows = []
    bucket_counts: dict[str, int] = collections.Counter()
    bucket_geoms: dict[str, list[str]] = collections.defaultdict(list)

    for name, geom in sub_scene.geometry.items():
        if not isinstance(geom, trimesh.Trimesh):
            continue
        centroid, normal, area = face_centroid_normal_area(geom)
        if area <= 0:
            continue
        d, dot, ref_idx = grid.query_aligned(centroid, normal, normal_min_dot,
                                             max_search_radius_cells=2)
        normal_angle_deg = float(np.degrees(np.arccos(np.clip(dot, 0.0, 1.0))))

        # plane_offset: how far the subject centroid sits OUT of the
        # nearest reference triangle's plane. Captures the "tilted face"
        # case better than centroid-distance alone — a face can be a
        # few mm offset along its own normal while staying close in
        # centroid distance.
        plane_offset = float("inf")
        if ref_idx >= 0:
            ref_n = ref_normals[ref_idx]
            ref_c = ref_centroids[ref_idx]
            plane_offset = float(abs(np.dot(centroid - ref_c, ref_n)))

        bucket = classify(d, normal_angle_deg, args.pos_tol, args.ang_tol)
        bucket_counts[bucket] += 1
        bucket_geoms[bucket].append(name)
        rows.append(dict(
            name=name,
            centroid=centroid.tolist(),
            normal=normal.tolist(),
            area_mm2=area,
            tris=int(geom.faces.shape[0]),
            ref_centroid_offset_mm=d,
            ref_normal_angle_deg=normal_angle_deg,
            plane_offset_mm=plane_offset,
            bucket=bucket,
        ))

    print()
    print("=== bucket counts ===")
    total = sum(bucket_counts.values())
    for b in ("OK", "POS_OK_ROT_BAD", "POS_BAD_ROT_OK", "WHOLLY_BAD"):
        n = bucket_counts.get(b, 0)
        pct = 100.0 * n / total if total else 0.0
        print(f"  {b:18s}  {n:>5d}  ({pct:5.1f}%)")

    # Worst-offender samples per bucket
    rows.sort(key=lambda r: (-r["ref_normal_angle_deg"], -r["ref_centroid_offset_mm"]))
    print()
    print("=== worst 20 by normal-angle deviation ===")
    print(f"{'name':<22s}  {'tris':>5s}  {'area':>8s}  {'pos_off':>8s}  {'normal°':>8s}  {'plane°':>8s}  bucket")
    for r in rows[:20]:
        print(f"{r['name']:<22s}  {r['tris']:>5d}  {r['area_mm2']:>8.0f}  "
              f"{r['ref_centroid_offset_mm']:>8.2f}  {r['ref_normal_angle_deg']:>8.2f}  "
              f"{r['plane_offset_mm']:>8.2f}  {r['bucket']}")

    # Cluster POS_OK_ROT_BAD by approximate normal-flip pattern. If
    # most rotated faces share a similar centroid-relative offset
    # vector, that's a sign of a global axis-flip somewhere.
    rotbad = [r for r in rows if r["bucket"] == "POS_OK_ROT_BAD"]
    if rotbad:
        print()
        print(f"=== POS_OK_ROT_BAD normal-angle histogram ({len(rotbad)} faces) ===")
        bins = [0, 5, 15, 30, 60, 90, 120, 180.01]
        hist, _ = np.histogram([r["ref_normal_angle_deg"] for r in rotbad], bins=bins)
        for lo, hi, c in zip(bins[:-1], bins[1:], hist):
            bar = "█" * int(40 * c / max(hist.max(), 1))
            print(f"  [{lo:>5.1f}°, {hi:>5.1f}°)  {c:>5d}  {bar}")

    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        # Per-bucket GLB
        for bucket, names in bucket_geoms.items():
            if bucket == "OK" or not names:
                continue
            scene = trimesh.Scene()
            for name in names:
                g = sub_scene.geometry.get(name)
                if g is not None:
                    scene.add_geometry(g.copy(), geom_name=name)
            path = args.out / f"{bucket.lower()}.glb"
            scene.export(path)
            print(f"\n[write] {path}  ({len(names)} faces)")
        # Full JSON for downstream analysis
        json_path = args.out / "per_face.json"
        json_path.write_text(json.dumps(rows, indent=2))
        print(f"[write] {json_path}")


if __name__ == "__main__":
    main()
