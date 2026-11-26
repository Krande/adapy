/**
 * The plugins' REFS in the builder scene (state/cellbuilder/refsSlice.ts).
 *
 * Owns: one box per ref -- its outline and a faint fill, in the ref's colour or
 * the builder's ref colour -- in its own subgroup beside the cells, so a cell
 * rebuild leaves them be and they are never a cell's snap target, never framed
 * and never counted in a cell's bounds. A ref is drawn as it is given: for look
 * "face" the plugin gives a thin box; look "none" makes no mesh at all -- it
 * stays in the store (`refs`), so it is still listed and selectable from the
 * model tree and shown in Properties, but there is nothing in `refMeshById` to
 * pick in the scene, frame, or style. The boxes are pickable (pointerTools) and
 * keep one material per box face, so a face drag resizes a ref as it does a cell.
 * Dimmed when the build leaves it out (as the include view says), white-edged
 * when selected, and shown only while the cells are.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import type {BuilderRef} from "@/state/cellbuilder/refsSlice";
import {useIncludeViewStore} from "@/state/includeViewStore";
import {requestRender} from "@/state/perfStore";
import {includeLook} from "@/utils/cellbuilder/include";
import {refIncludeState} from "@/utils/cellbuilder/refInclude";
import {BOX_FACE_SIDES} from "@/utils/cellbuilder/snap";
import {EXCLUDED_DASH, EXCLUDED_DIM, EXCLUDED_GAP, REF_COLOR, REF_OPACITY, REF_SELECTED_OPACITY} from "./sceneConstants";
import type {CellBuilderScene} from "./sceneContext";
import {disposeMesh} from "./overlays";
import {boxStructure, structureFrame} from "./structureFrames";
import {clipWithModel} from "@/utils/scene/section_clipping";

/** The ref's colour as a number; the builder's ref colour when it has none or one three.js can not read. */
export const refColour = (ref: Pick<BuilderRef, "color">): number => {
    if (!ref.color) return REF_COLOR;
    try {
        return new THREE.Color(ref.color).getHex();
    } catch {
        return REF_COLOR;
    }
};

const dropRefMeshes = (ctx: CellBuilderScene) => {
    for (let i = ctx.refsGroup.children.length - 1; i >= 0; i--) {
        const o = ctx.refsGroup.children[i];
        o.traverse((m: any) => {
            if (m.isMesh || m.isLineSegments) disposeMesh(ctx, m);
        });
        ctx.refsGroup.remove(o);
    }
    ctx.refMeshById.clear();
};

/** Draw every ref again from the store. */
export const rebuildRefs = (ctx: CellBuilderScene) => {
    dropRefMeshes(ctx);
    const st = useCellBuilderStore.getState();
    const view = useIncludeViewStore.getState().view;
    if (st.active) {
        for (const ref of Object.values(st.refs)) {
            if (ref.look === "none") continue;
            const look = includeLook(refIncludeState(ref, st.groups), view);
            if (!look.visible) continue;
            const color = refColour(ref);
            // A zero-thickness box draws no faces: keep a sliver, so a face ref still shows its fill.
            const size = ref.size.map((v) => Math.max(Math.abs(v), 1e-3)) as [number, number, number];
            const geo = new THREE.BoxGeometry(...size);
            const mats = BOX_FACE_SIDES.map(
                () => new THREE.MeshBasicMaterial({color, transparent: true, opacity: REF_OPACITY, depthWrite: false}),
            );
            const mesh = new THREE.Mesh(geo, mats);
            mesh.position.set(ref.origin[0] + ref.size[0] / 2, ref.origin[1] + ref.size[1] / 2, ref.origin[2] + ref.size[2] / 2);
            mesh.userData.__refId = ref.id;
            mesh.userData.__includeLook = look;
            mesh.userData.__excludeFromFit = true;
            const edges = new THREE.LineSegments(
                new THREE.EdgesGeometry(geo),
                look.dimmed
                    ? new THREE.LineDashedMaterial({color, dashSize: EXCLUDED_DASH, gapSize: EXCLUDED_GAP, transparent: true, opacity: 0.7})
                    : new THREE.LineBasicMaterial({color}),
            );
            if (look.dimmed) edges.computeLineDistances();
            mesh.add(edges);
            // In its structure's frame (./structureFrames.ts): a ref is given relative to it as a cell is.
            structureFrame(ctx.refsGroup, boxStructure(st, {ref}), st.groups).add(mesh);
            ctx.refMeshById.set(ref.id, mesh);
        }
    }
    ctx.refsGroup.visible = st.cellsVisible;
    refreshRefStyles(ctx);
    applyRefVisibility(ctx);
    // Section planes: materials are re-created above on every rebuild, so they must be
    // re-tagged and re-clipped every time, not just when a plane is added/moved. refreshRefStyles
    // only mutates colour/opacity on these same material instances, so it needs no clipping of its own.
    clipWithModel(ctx.refsGroup);
};

// Per-ref hide (the ref analogue of meshRebuild's applyCellVisibility): a hidden ref's box
// is invisible AND non-pickable (pickRefMesh filters on mesh.visible), so a click on it falls
// through to whatever sits underneath -- the space it is on, or the compiled result.
export const applyRefVisibility = (ctx: CellBuilderScene) => {
    const hidden = useCellBuilderStore.getState().hiddenRefIds;
    for (const [refId, mesh] of ctx.refMeshById) {
        mesh.visible = !hidden.includes(refId);
    }
};

/** Recolour the refs for the selection: every selected one fuller, its edges white -- the primary
 *  (`selection`) as well as the rest of a multi-selection (`selectedRefIds`): a ref added by
 *  Ctrl-click or a box select stayed unstyled, only the single most-recently-clicked one lighting
 *  up, even though the header counted it as selected too. */
export const refreshRefStyles = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const sel = st.selection;
    for (const [refId, mesh] of ctx.refMeshById) {
        const ref = st.refs[refId];
        if (!ref) continue;
        const selected = (sel?.kind === "ref" && sel.refId === refId) || st.selectedRefIds.includes(refId);
        const dim = (mesh.userData.__includeLook as {dimmed?: boolean} | undefined)?.dimmed ? EXCLUDED_DIM : 1;
        const color = refColour(ref);
        for (const m of mesh.material as THREE.MeshBasicMaterial[]) {
            m.color.setHex(color);
            m.opacity = (selected ? REF_SELECTED_OPACITY : REF_OPACITY) * dim;
        }
        const edges = mesh.children[0] as THREE.LineSegments | undefined;
        if (edges) (edges.material as THREE.LineBasicMaterial).color.setHex(selected ? 0xffffff : color);
    }
    requestRender();
};

/** Take the refs out of the scene (the builder's teardown). */
export const disposeRefs = (ctx: CellBuilderScene) => dropRefMeshes(ctx);
