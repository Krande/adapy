/**
 * "Show as CAD" per-object PREVIEWS.
 *
 * Owns: lazily fetching and caching an equipment type's preview GLB, seating a
 * clone at the cell placement (min corner to cell corner, spun about the
 * footprint centre), and the CAD-bounds lookups the box preview and the port
 * snapping read — an equipment whose CAD is loaded shows a box fitted to the
 * real geometry, not its declared extents.
 */

import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {ungzip} from "pako";
import {capabilities} from "@/services/capabilities";
import {useCellBuilderStore, type BuilderCell} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {cadDisplayBox, type CellBox, type Vec3} from "@/utils/cellbuilder/snap";
import {offsetVec, type CellBuilderScene} from "./sceneContext";
import {applyCellVisibility} from "./meshRebuild";

export const previewScope = (ctx: CellBuilderScene): string => {
    const s = useScopeStore.getState().current;
    return s ? scopeUrlPart(s) : "user:me";
};

// The catalog type id for an equipment cell (needed to fetch its preview
// GLB), resolved by slug then name like portsForEquipment. Built-in code
// archetypes have no id (and no CAD), so those yield null.
export const cadTypeIdForCell = (ctx: CellBuilderScene, cell: BuilderCell): string | null => {
    if (cell.kind !== "equipment" || !cell.equipmentType) return null;
    const types = useCellBuilderStore.getState().equipmentTypes;
    const key = cell.equipmentType.toLowerCase();
    const t =
        types.find((o) => o.slug.toLowerCase() === key) ?? types.find((o) => o.name.toLowerCase() === key);
    return t?.id ?? null;
};

// Parse an equipment preview GLB blob into a group (gzip-sniffed, like the
// catalogue preview + the result loader). Displayed in its NATIVE orientation
// (no re-orientation) and its placement is fit from its measured bounds — we
// do NOT assume the (possibly old) preview GLB is Z-up.
export const parseCadGlb = async (ctx: CellBuilderScene, buf: ArrayBuffer): Promise<THREE.Group | null> => {
    let bytes: Uint8Array<ArrayBufferLike> = new Uint8Array(buf);
    if (bytes.length > 2 && bytes[0] === 0x1f && bytes[1] === 0x8b) bytes = ungzip(bytes);
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    return await new Promise((resolve) => {
        new GLTFLoader().parse(
            ab,
            "",
            (gltf) => resolve(gltf.scene),
            () => resolve(null),
        );
    });
};

// Lazily fetch + cache a type's preview GLB, then re-run the CAD rebuild.
export const ensureCadLoaded = (ctx: CellBuilderScene, typeId: string) => {
    if (ctx.cadPreviewCache.has(typeId)) return;
    ctx.cadPreviewCache.set(typeId, "loading");
    void (async () => {
        try {
            const scope = previewScope(ctx);
            const buf = await capabilities.procedural.fetchEquipmentPreviewGlb(scope, typeId);
            if (!buf) {
                ctx.cadPreviewCache.set(typeId, "error");
            } else {
                const g = await parseCadGlb(ctx, buf);
                ctx.cadPreviewCache.set(typeId, g ?? "error");
            }
        } catch {
            ctx.cadPreviewCache.set(typeId, "error");
        }
        rebuildCadPreviews(ctx);
    })();
};

// Clone the cached prototype and seat its min corner at the cell origin (the
// compiler's "min corner → cell corner" convention), then spin it about the
// footprint centre to match the cell rotation — mirroring the box placement.
export const placeCadPreview = (ctx: CellBuilderScene, proto: THREE.Group, cell: BuilderCell): THREE.Object3D | null => {
    const g = proto.clone(true);
    g.position.set(0, 0, 0);
    g.rotation.set(0, 0, 0);
    g.updateWorldMatrix(true, true);
    const b = new THREE.Box3().setFromObject(g);
    if (b.isEmpty()) return null;
    const seat = new THREE.Vector3(cell.origin[0] - b.min.x, cell.origin[1] - b.min.y, cell.origin[2] - b.min.z);
    g.position.copy(seat);
    const rot = cell.rotation;
    if (rot && (rot[0] || rot[1] || rot[2])) {
        const euler = new THREE.Euler(
            THREE.MathUtils.degToRad(rot[0]),
            THREE.MathUtils.degToRad(rot[1]),
            THREE.MathUtils.degToRad(rot[2]),
            "ZYX",
        );
        const pivot = new THREE.Vector3(
            cell.origin[0] + cell.size[0] / 2,
            cell.origin[1] + cell.size[1] / 2,
            cell.origin[2],
        );
        const holder = new THREE.Group();
        holder.position.copy(pivot);
        holder.setRotationFromEuler(euler);
        g.position.sub(pivot); // re-express seat relative to the pivot holder
        holder.add(g);
        return holder;
    }
    return g;
};

export const rebuildCadPreviews = (ctx: CellBuilderScene) => {
    // Clones share the cached prototype's geometry/materials — remove them
    // without disposing (the cache owns those resources; freed at teardown).
    for (let i = ctx.cadPreviewGroup.children.length - 1; i >= 0; i--) {
        ctx.cadPreviewGroup.remove(ctx.cadPreviewGroup.children[i]);
    }
    ctx.cadPreviewShown.clear();
    const st = useCellBuilderStore.getState();
    if (st.active) {
        for (const cellId of st.cadPreviewCells) {
            const cell = st.cells[cellId];
            if (!cell || cell.kind !== "equipment") continue;
            const typeId = cadTypeIdForCell(ctx, cell);
            if (!typeId) continue;
            const cached = ctx.cadPreviewCache.get(typeId);
            if (cached === undefined) {
                ensureCadLoaded(ctx, typeId);
                continue;
            }
            if (cached === "loading" || cached === "error") continue;
            const placed = placeCadPreview(ctx, cached, cell);
            if (placed) {
                ctx.cadPreviewGroup.add(placed);
                ctx.cadPreviewShown.add(cellId);
            }
        }
    }
    ctx.cadPreviewGroup.visible = st.cellsVisible;
    applyCellVisibility(ctx);
    requestRender();
};

// ArrowHelper owns a Line (non-LineSegments) + a Mesh; the generic
// container-dispose loop only frees meshes/line-segments, so free both parts
// explicitly here.
export const findCadMesh = (ctx: CellBuilderScene, cell: BuilderCell): THREE.Mesh | null => {
    let mesh: THREE.Mesh | null = null;
    (getViewerRuntime().scene.current ?? ctx.scene).traverse((o) => {
        if (mesh) return;
        if ((o as THREE.Mesh).isMesh && o.name && o.name === cell.name) mesh = o as THREE.Mesh;
    });
    return mesh;
};

// The model-space AABB of the CAD mesh loaded for this equipment cell, or
// null when "Use CAD models" is off / no matching mesh is in the scene.
// Rotation is already baked into the compiled CAD, so this axis-aligned box
// wraps the placed (rotated) geometry directly. offsetVec() unmaps the
// viewer's model translation so the result is in the same cell-origin frame
// as cell.origin/size.
export const cadBoundsForCell = (ctx: CellBuilderScene, cell: BuilderCell): {min: Vec3; max: Vec3} | null => {
    const st = useCellBuilderStore.getState();
    if (cell.kind !== "equipment" || !st.equipmentCad) return null;
    const mesh = findCadMesh(ctx, cell);
    if (!mesh) return null;
    // Refresh the world matrix chain so a just-loaded mesh reports correct
    // bounds (expandByObject only refreshes the object's own matrix).
    mesh.updateWorldMatrix(true, false);
    const b = new THREE.Box3().setFromObject(mesh, true);
    if (b.isEmpty()) return null;
    const off = offsetVec(ctx);
    return {
        min: [b.min.x - off.x, b.min.y - off.y, b.min.z - off.z],
        max: [b.max.x - off.x, b.max.y - off.y, b.max.z - off.z],
    };
};

// The box to draw + interact with for a cell: an equipment's CAD-fitted AABB
// when a linked CAD mesh is loaded, else the declared LX/LY/LZ box. `cadFitted`
// flags the CAD case so callers can skip re-applying the cell rotation (it is
// already baked into the CAD bounds).
export const displayBoxForCell = (ctx: CellBuilderScene, cell: BuilderCell): {box: CellBox; cadFitted: boolean} => {
    const bounds = cadBoundsForCell(ctx, cell);
    return {box: cadDisplayBox(cell, bounds), cadFitted: bounds !== null};
};

export const collectCadVerts = (ctx: CellBuilderScene, cell: BuilderCell): Vec3[] => {
    const st = useCellBuilderStore.getState();
    if (!st.equipmentCad) return [];
    const mesh = findCadMesh(ctx, cell);
    if (!mesh) return [];
    const foundMesh: THREE.Mesh = mesh;
    const posAttr = (foundMesh.geometry as THREE.BufferGeometry).getAttribute("position");
    if (!posAttr) return [];
    const off = offsetVec(ctx);
    const MAX = 4000;
    const stride = Math.max(1, Math.floor(posAttr.count / MAX));
    const v = new THREE.Vector3();
    const out: Vec3[] = [];
    for (let i = 0; i < posAttr.count; i += stride) {
        v.fromBufferAttribute(posAttr, i).applyMatrix4(foundMesh.matrixWorld);
        out.push([v.x - off.x, v.y - off.y, v.z - off.z]);
    }
    return out;
};

// The port snap target (bbox corner or CAD vertex, model space) nearest the
// pointer on screen within SNAP_PX, or null — the port analogue of
// nearestCornerToPointer.
