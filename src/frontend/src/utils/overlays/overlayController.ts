// Draws the Scene > Overlays marks over the loaded models.
//
// Reads the relations of every model loaded from a published asset (one batched lookup per
// collection, by the full names of its rows -- the names the scene tree already carries), places
// marks from each element's drawn bounds (`overlayModel`), and keeps one group of lines and cones
// in the scene. Re-read when the tree changes while an overlay is on; redrawn when a toggle flips.

import * as THREE from "three";

import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { nodeRelationsApi, type NodeRelation } from "@/services/api/nodeRelations";
import { useAssetBrowserStore } from "@/state/assetBrowserStore";
import { anyOverlayOn, useOverlayStore } from "@/state/overlayStore";
import { requestRender } from "@/state/perfStore";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import { useTreeViewStore } from "@/state/treeViewStore";
import { getViewerRuntime } from "@/state/viewerRuntime";
import type { Aabb } from "@/utils/bboxSelection/boxMath";
import { boundsOfRanges } from "@/utils/bboxSelection/rangeBounds";
import { refOfSourceName } from "@/utils/groups/savedGroups";
import { rangesUnder } from "@/utils/tree_view/sceneTreeActions";

import { overlayMarks, type OverlayMarks, type Vec3 } from "./overlayModel";

const GROUP_NAME = "ada-scene-overlays";
const FLOW_COLOUR = "#22d3ee";
const MODELLED_COLOUR = "#fbbf24";
const PENETRATION_COLOUR = "#fb923c";
const CONNECTION_COLOUR = "#a3e635";

/** Every model loaded from a published asset: its collection and its rows by name (first wins). */
function assetModels(root: TreeNodeData | null): { collection: string; rows: Map<string, TreeNodeData> }[] {
  if (!root) return [];
  const loaded = useAssetBrowserStore.getState().loaded;
  const out = [];
  for (const model of root.children ?? []) {
    const source = model.source_name;
    if (!source || !source.startsWith("assets:")) continue;
    const ref = refOfSourceName(source, loaded);
    if (!ref) continue;
    const rows = new Map<string, TreeNodeData>();
    const stack = [model];
    while (stack.length) {
      const n = stack.pop()!;
      if (n.name && !rows.has(n.name)) rows.set(n.name, n);
      for (const c of n.children ?? []) stack.push(c);
    }
    out.push({ collection: ref.collection.toLowerCase(), rows });
  }
  return out;
}

function boundsOfRow(row: TreeNodeData): Aabb | null {
  let out: Aabb | null = null;
  for (const [mesh, ids] of rangesUnder([row])) {
    const b = boundsOfRanges(mesh, ids);
    if (!b) continue;
    if (!out) out = { min: [...b.min], max: [...b.max] };
    else
      for (let k = 0; k < 3; k++) {
        out.min[k] = Math.min(out.min[k], b.min[k]);
        out.max[k] = Math.max(out.max[k], b.max[k]);
      }
  }
  return out;
}

let generation = 0;

/** Read the relations of what is loaded and redraw. A later call supersedes an earlier one. */
export async function refreshOverlays(): Promise<void> {
  const store = useOverlayStore.getState();
  if (!anyOverlayOn(store.enabled)) {
    drawOverlays();
    return;
  }
  const mine = ++generation;
  const models = assetModels(useTreeViewStore.getState().treeData);
  store.setResult({ status: "loading", error: null });
  const scope = scopeUrlPart(useScopeStore.getState().current);
  const relations: NodeRelation[] = [];
  const collections: string[] = [];
  const notIndexed: string[] = [];
  const rows = new Map<string, TreeNodeData>();
  try {
    const byCollection = new Map<string, string[]>();
    for (const m of models) {
      for (const [name, row] of m.rows) if (!rows.has(name)) rows.set(name, row);
      byCollection.set(m.collection, [...(byCollection.get(m.collection) ?? []), ...m.rows.keys()]);
    }
    for (const [collection, names] of byCollection) {
      const answer = await nodeRelationsApi.relationsFrom(scope, collection, [...new Set(names)]);
      if (mine !== generation) return;
      collections.push(collection);
      if (!answer.indexed) notIndexed.push(collection);
      relations.push(...answer.relations);
    }
  } catch (err) {
    if (mine !== generation) return;
    useOverlayStore.getState().setResult({ status: "error", error: err instanceof Error ? err.message : String(err) });
    return;
  }
  const bounds = new Map<string, Aabb | null>();
  const boundsOf = (name: string): Aabb | null => {
    if (!bounds.has(name)) {
      const row = rows.get(name);
      bounds.set(name, row ? boundsOfRow(row) : null);
    }
    return bounds.get(name)!;
  };
  const marks = overlayMarks(relations, boundsOf);
  useOverlayStore.getState().setResult({ status: "ready", marks, collections, notIndexed });
  drawOverlays();
}

function boxEdges(b: Aabb, out: number[]): void {
  const [x0, y0, z0] = b.min;
  const [x1, y1, z1] = b.max;
  const c: Vec3[] = [
    [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
    [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
  ]; // prettier-ignore
  for (const [i, j] of [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]])
    out.push(...c[i], ...c[j]);
}

function lines(positions: number[], colours: number[] | string): THREE.LineSegments {
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  const vertexColours = Array.isArray(colours);
  if (vertexColours) geom.setAttribute("color", new THREE.Float32BufferAttribute(colours, 3));
  const mat = new THREE.LineBasicMaterial({
    vertexColors: vertexColours,
    color: vertexColours ? 0xffffff : colours,
    depthTest: false,
    transparent: true,
    opacity: 0.9,
  });
  const seg = new THREE.LineSegments(geom, mat);
  seg.renderOrder = 999;
  return seg;
}

function arrows(marks: OverlayMarks): THREE.Object3D | null {
  const list = marks.flow.arrows;
  if (!list.length) return null;
  const group = new THREE.Group();
  const shafts: number[] = [];
  const shaftColours: number[] = [];
  const cone = new THREE.ConeGeometry(0.5, 1, 12);
  cone.translate(0, -0.5, 0); // tip at the origin
  const mesh = new THREE.InstancedMesh(
    cone,
    new THREE.MeshBasicMaterial({ depthTest: false, transparent: true, opacity: 0.95 }),
    list.length,
  );
  mesh.renderOrder = 1000;
  const up = new THREE.Vector3(0, 1, 0);
  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const colour = new THREE.Color();
  list.forEach((a, i) => {
    const from = new THREE.Vector3(...a.from);
    const to = new THREE.Vector3(...a.to);
    const d = to.clone().sub(from);
    const len = d.length();
    colour.set(a.basis === "flow" ? FLOW_COLOUR : MODELLED_COLOUR);
    shafts.push(...a.from, ...a.to);
    shaftColours.push(colour.r, colour.g, colour.b, colour.r, colour.g, colour.b);
    const size = Math.max(len * 0.18, 1e-3);
    q.setFromUnitVectors(up, len > 0 ? d.normalize() : up);
    const tip = from.clone().lerp(to, 0.5).addScaledVector(d, size / 2);
    m.compose(tip, q, new THREE.Vector3(size * 0.6, size, size * 0.6));
    mesh.setMatrixAt(i, m);
    mesh.setColorAt(i, colour);
  });
  group.add(lines(shafts, shaftColours), mesh);
  return group;
}

/** Replace the overlay group with the enabled overlays' marks. */
export function drawOverlays(): void {
  const scene = getViewerRuntime().scene.current;
  if (!scene) return;
  const old = scene.getObjectByName(GROUP_NAME);
  if (old) {
    scene.remove(old);
    old.traverse((o) => {
      const any = o as THREE.Mesh;
      any.geometry?.dispose();
      const mat = any.material as THREE.Material | THREE.Material[] | undefined;
      if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
      else mat?.dispose();
    });
  }
  const { enabled, marks } = useOverlayStore.getState();
  const group = new THREE.Group();
  group.name = GROUP_NAME;
  if (enabled.systems && marks.systems.boxes.length) {
    const pos: number[] = [];
    const col: number[] = [];
    const c = new THREE.Color();
    for (const { box, colour } of marks.systems.boxes) {
      const before = pos.length;
      boxEdges(box, pos);
      c.set(colour);
      for (let i = before; i < pos.length; i += 3) col.push(c.r, c.g, c.b);
    }
    group.add(lines(pos, col));
  }
  if (enabled.flow) {
    const a = arrows(marks);
    if (a) group.add(a);
  }
  if (enabled.penetrations && marks.penetrations.boxes.length) {
    const pos: number[] = [];
    for (const b of marks.penetrations.boxes) boxEdges(b, pos);
    for (const l of marks.penetrations.links) pos.push(...l.from, ...l.to);
    group.add(lines(pos, PENETRATION_COLOUR));
  }
  if (enabled.connections && marks.connections.links.length) {
    const pos: number[] = [];
    for (const l of marks.connections.links) pos.push(...l.from, ...l.to);
    group.add(lines(pos, CONNECTION_COLOUR));
  }
  if (group.children.length) scene.add(group);
  requestRender();
}

let unsubscribe: (() => void) | null = null;
let pending: ReturnType<typeof setTimeout> | null = null;

/** Keep the overlays in step with the store and the scene tree. Idempotent; returns the stop. */
export function bindOverlays(): () => void {
  if (unsubscribe) return unsubscribe;
  const schedule = () => {
    if (pending) clearTimeout(pending);
    pending = setTimeout(() => {
      pending = null;
      void refreshOverlays();
    }, 300);
  };
  const offTree = useTreeViewStore.subscribe((s, prev) => {
    if (s.treeData !== prev.treeData && anyOverlayOn(useOverlayStore.getState().enabled)) schedule();
  });
  const offStore = useOverlayStore.subscribe((s, prev) => {
    if (s.enabled === prev.enabled) return;
    const turnedOn = anyOverlayOn(s.enabled) && (s.status === "idle" || s.status === "error");
    if (turnedOn) schedule();
    else drawOverlays();
  });
  unsubscribe = () => {
    offTree();
    offStore();
    if (pending) clearTimeout(pending);
    unsubscribe = null;
  };
  return unsubscribe;
}
