import * as THREE from "three";

import { sceneRef } from "@/state/refs";
import { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";
import { useTreeViewStore } from "@/state/treeViewStore";
import type { TreeNodeData } from "@/components/tree_view/CustomNode";

// Resolve where a system's *actual routed pipe* sits in the compiled GLB so an
// overlay icon can hug the real geometry instead of the system's equipment
// bounds. The routed run is emitted as ``ada.Pipe("<System>_route", …)`` whose
// straight/elbow segments are named ``<System>_route_<n>`` (base_piping's
// seg_names counter). Those names survive into the model tree, so we can join
// tree name → (model_key, rangeId) → CustomBatchedMesh draw-range → vertices.

// Every batched mesh currently in the scene. A single model exports as several
// CustomBatchedMeshes (node0, node1, …) that ALL share the same unique_key (the
// model hash) but carry disjoint drawRanges, so we must scan them rather than
// key a Map by unique_key (which would collapse them and drop whichever mesh
// actually holds the pipe ranges).
function allBatchedMeshes(): CustomBatchedMesh[] {
  const out: CustomBatchedMesh[] = [];
  sceneRef.current?.traverse((o) => {
    if (o instanceof CustomBatchedMesh) out.push(o);
  });
  return out;
}

// Every (model_key, rangeId) whose tree name belongs to ``<systemName>_route``.
// Matches the pipe node itself and its numbered segments, but not a different
// system whose name merely shares a prefix (e.g. "Power" vs "PowerFeed").
function routeRanges(
  systemName: string,
  root: TreeNodeData | null,
): Array<[string, string]> {
  if (!root) return [];
  const exact = `${systemName}_route`;
  const prefix = `${systemName}_route_`;
  const hits: Array<[string, string]> = [];
  const stack: TreeNodeData[] = [root];
  while (stack.length) {
    const n = stack.pop()!;
    if (
      n.rangeId != null &&
      n.model_key != null &&
      (n.name === exact || n.name.startsWith(prefix))
    ) {
      hits.push([String(n.model_key), String(n.rangeId)]);
    }
    if (Array.isArray(n.children)) for (const c of n.children) stack.push(c);
  }
  return hits;
}

// World-space centroid of a system's routed pipe geometry, or null when the
// compiled pipe isn't in the scene yet (pre-compile, or no matching run). For a
// swept tube the surface-vertex centroid lands on the centerline, so the icon
// ends up sitting naturally over the pipe rather than over the system bounds.
export function pipeCentroidWorld(systemName: string): THREE.Vector3 | null {
  const treeData = useTreeViewStore.getState().treeData;
  const ranges = routeRanges(systemName, treeData);
  if (ranges.length === 0) return null;

  const meshes = allBatchedMeshes();
  const acc = new THREE.Vector3();
  const v = new THREE.Vector3();
  let n = 0;

  for (const [key, rangeId] of ranges) {
    // Find the specific mesh that owns this range — same model key AND holds the
    // rangeId (a model's node0/node1 meshes share the key but split the ranges).
    const mesh = meshes.find(
      (m) => m.unique_key === key && m.drawRanges.has(rangeId),
    );
    if (!mesh) continue;
    const range = mesh.drawRanges.get(rangeId);
    if (!range) continue;
    const [start, count] = range;
    const index = mesh.geometry.getIndex();
    const pos = mesh.geometry.getAttribute("position") as
      | THREE.BufferAttribute
      | undefined;
    if (!index || !pos) continue;

    mesh.updateWorldMatrix(true, false);
    const end = Math.min(start + count, index.count);
    // Sub-sample dense tubes — a few hundred corners already pin the centroid,
    // and the icon only needs a representative point, not an exact centroid.
    const step = Math.max(1, Math.floor((end - start) / 600));
    for (let i = start; i < end; i += step) {
      const vid = index.getX(i);
      v.fromBufferAttribute(pos, vid).applyMatrix4(mesh.matrixWorld);
      acc.add(v);
      n++;
    }
  }

  if (n === 0) return null;
  return acc.multiplyScalar(1 / n);
}
