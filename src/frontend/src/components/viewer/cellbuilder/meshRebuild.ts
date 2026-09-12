/**
 * Cell MESH RECONCILIATION.
 *
 * Owns: rebuilding the editable cell meshes from the store (boxes for cells,
 * equipment and openings; swept band proxies for loft bays), drawing the
 * read-only companion models, and applying per-cell hide. Rebuilt wholesale on
 * change — the geometry is boxes and an incremental diff would buy complexity
 * against a cost nobody can measure.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useCompanionModelStore} from "@/state/companionModelStore";
import {requestRender} from "@/state/perfStore";
import {bandFaceIds} from "@/utils/cellbuilder/loft";
import {BOX_FACE_SIDES} from "@/utils/cellbuilder/snap";
import {BASE_OPACITY, LOFT_COLOR, LOFT_OPACITY, colorForKind} from "./sceneConstants";
import type {CellBuilderScene} from "./sceneContext";
import {ringsEdgesGeometry, sweptBandGeometry, sweptBandGroupedGeometry} from "./geometryHelpers";
import {disposeMesh, refreshFaceStyles} from "./overlays";
import {displayBoxForCell} from "./cadPreview";

/** Draw every companion showing topology as plain, non-interactive boxes.
 *
 * Rebuilt wholesale on change: a companion set is a handful of models, the
 * geometry is boxes, and an incremental diff here would be complexity
 * bought against a cost nobody can measure. */
export const rebuildCompanions = (ctx: CellBuilderScene) => {
    for (let i = ctx.companionsGroup.children.length - 1; i >= 0; i--) {
        const o = ctx.companionsGroup.children[i];
        o.traverse((m: any) => {
            if (m.isMesh || m.isLineSegments) disposeMesh(ctx, m);
        });
        ctx.companionsGroup.remove(o);
    }

    const {companions} = useCompanionModelStore.getState();
    for (const c of Object.values(companions)) {
        if (c.rep !== "topology") continue;
        // One group per model so its offset is a single transform rather
        // than baked into every box — moving it is then a position write.
        const modelGroup = new THREE.Group();
        modelGroup.name = `__companion__${c.modelId}`;
        modelGroup.position.x = c.offsetX;
        for (const cell of c.cells) {
            const geo = new THREE.BoxGeometry(cell.size[0], cell.size[1], cell.size[2]);
            const mesh = new THREE.Mesh(
                geo,
                new THREE.MeshBasicMaterial({
                    color: colorForKind(cell.kind),
                    transparent: true,
                    // Dimmer than the edited model: at a glance, which one
                    // you are editing must be unambiguous.
                    opacity: BASE_OPACITY * 0.45,
                    depthWrite: false,
                }),
            );
            mesh.position.set(
                cell.origin[0] + cell.size[0] / 2,
                cell.origin[1] + cell.size[1] / 2,
                cell.origin[2] + cell.size[2] / 2,
            );
            // Not pickable, not fittable: a companion is scenery.
            mesh.raycast = () => {};
            mesh.userData.__excludeFromFit = true;
            modelGroup.add(mesh);
        }
        ctx.companionsGroup.add(modelGroup);
    }
    requestRender();
};

export const rebuild = (ctx: CellBuilderScene) => {
    for (let i = ctx.cellsGroup.children.length - 1; i >= 0; i--) {
        const o = ctx.cellsGroup.children[i];
        o.traverse((m: any) => {
            if (m.isMesh || m.isLineSegments) disposeMesh(ctx, m);
        });
        ctx.cellsGroup.remove(o);
    }
    ctx.meshById.clear();
    ctx.hovered = null;
    ctx.hoveredEdge = null;

    const st = useCellBuilderStore.getState();
    if (st.active) {
        for (const cell of Object.values(st.cells)) {
            // Loft band: a read-only swept proxy drawn from its two profile
            // rings, with the two station rings as the edge overlay. Same
            // meshById/__cellId plumbing as a box, so click-select + hide +
            // the whole-cell highlight all work identically — only the
            // geometry source differs (band instead of BoxGeometry).
            if (cell.kind === "loft" && cell.loft) {
                const [lo, hi] = cell.loft.rings;
                // Per-face pickable when the two rings match (homogeneous
                // rectangle/circle band): one material per side panel + the
                // two caps, so a face pick resolves to a loft face id. A
                // mismatched-count band degrades to a single translucent
                // material (whole-band pick only). DoubleSide so winding is moot.
                const grouped = sweptBandGroupedGeometry(lo, hi);
                const loftMat = () =>
                    new THREE.MeshBasicMaterial({
                        color: LOFT_COLOR,
                        transparent: true,
                        opacity: LOFT_OPACITY,
                        depthWrite: false,
                        side: THREE.DoubleSide,
                    });
                let mesh: THREE.Mesh;
                if (grouped) {
                    const {edges, caps} = bandFaceIds(cell.loft);
                    const faceIds = [...edges, caps[0], caps[1]];
                    mesh = new THREE.Mesh(
                        grouped,
                        faceIds.map(() => loftMat()),
                    );
                    // Member-relative loft face id per material index (drives
                    // per-face selection highlight + excluded-panel dimming).
                    mesh.userData.__loftFaceIds = faceIds;
                } else {
                    mesh = new THREE.Mesh(sweptBandGeometry(lo, hi), loftMat());
                }
                mesh.userData.__cellId = cell.id;
                const ringEdges = new THREE.LineSegments(
                    ringsEdgesGeometry(lo, hi),
                    new THREE.LineBasicMaterial({color: LOFT_COLOR}),
                );
                mesh.add(ringEdges);
                ctx.cellsGroup.add(mesh);
                ctx.meshById.set(cell.id, mesh);
                continue;
            }
            // A CAD-backed equipment fits its editable box to the loaded CAD
            // mesh's bounds (so the box wraps the real geometry, not the
            // declared LX/LY/LZ); everything else uses its declared box.
            const {box: dbox, cadFitted} = displayBoxForCell(ctx, cell);
            const geo = new THREE.BoxGeometry(...dbox.size);
            const color = colorForKind(cell.kind);
            // One material per BoxGeometry group (+X,-X,+Y,-Y,+Z,-Z) so a
            // single face can highlight on hover/selection.
            const mats = BOX_FACE_SIDES.map(
                () =>
                    new THREE.MeshBasicMaterial({
                        color,
                        transparent: true,
                        opacity: BASE_OPACITY,
                        depthWrite: false,
                    }),
            );
            const mesh = new THREE.Mesh(geo, mats);
            mesh.position.set(
                dbox.origin[0] + dbox.size[0] / 2,
                dbox.origin[1] + dbox.size[1] / 2,
                dbox.origin[2] + dbox.size[2] / 2,
            );
            // Equipment can carry a rotation (gizmo / manual panel). Spin the
            // box preview about the footprint centre so it matches the
            // compiled body; the box-centre orbits that pivot. A CAD-fitted
            // box already wraps the placed (rotated) geometry — its AABB has
            // the rotation baked in — so it stays axis-aligned here.
            const rot = cell.rotation;
            if (!cadFitted && cell.kind === "equipment" && rot && (rot[0] || rot[1] || rot[2])) {
                const euler = new THREE.Euler(
                    THREE.MathUtils.degToRad(rot[0]),
                    THREE.MathUtils.degToRad(rot[1]),
                    THREE.MathUtils.degToRad(rot[2]),
                    "ZYX",
                );
                mesh.quaternion.setFromEuler(euler);
                const pivot = new THREE.Vector3(
                    cell.origin[0] + cell.size[0] / 2,
                    cell.origin[1] + cell.size[1] / 2,
                    cell.origin[2],
                );
                const offset = new THREE.Vector3(0, 0, cell.size[2] / 2).applyEuler(euler);
                mesh.position.copy(pivot).add(offset);
            }
            mesh.userData.__cellId = cell.id;
            const edges = new THREE.LineSegments(
                new THREE.EdgesGeometry(geo),
                new THREE.LineBasicMaterial({color}),
            );
            mesh.add(edges);
            ctx.cellsGroup.add(mesh);
            ctx.meshById.set(cell.id, mesh);
        }
    }
    ctx.cellsGroup.visible = st.cellsVisible;
    applyCellVisibility(ctx);
    ctx.ghost.visible = false;
    ctx.ghostBox = null;
    refreshFaceStyles(ctx);
};

// Per-cell hide (the "Hide selected" analogue): a hidden cell's box is made
// invisible; pickBuilderMesh also drops it so it stops absorbing clicks.
export const applyCellVisibility = (ctx: CellBuilderScene) => {
    const hidden = useCellBuilderStore.getState().hiddenCellIds;
    for (const [cellId, mesh] of ctx.meshById) {
        // A cell shown as CAD hides its placeholder box (the CAD stands in).
        mesh.visible = !hidden.includes(cellId) && !ctx.cadPreviewShown.has(cellId);
    }
};

// ── "Show as CAD" per-object previews ─────────────────────────────
