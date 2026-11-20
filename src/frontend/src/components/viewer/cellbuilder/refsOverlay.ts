/**
 * The plugins' REFS in the builder scene (state/cellbuilder/refsSlice.ts).
 *
 * Owns: one box per ref -- its outline and a faint fill, in the ref's colour or
 * the builder's ref colour -- in its own subgroup beside the cells, so a cell
 * rebuild leaves them be and they are never a cell's snap target, never framed
 * and never counted in a cell's bounds. A ref is drawn as it is given: for look
 * "face" the plugin gives a thin box. The boxes are pickable (pointerTools) and
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
import {BOX_FACE_SIDES} from "@/utils/cellbuilder/snap";
import {EXCLUDED_DASH, EXCLUDED_DIM, EXCLUDED_GAP, REF_COLOR, REF_OPACITY, REF_SELECTED_OPACITY} from "./sceneConstants";
import type {CellBuilderScene} from "./sceneContext";
import {disposeMesh} from "./overlays";

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
            const look = includeLook(ref.include ? "included" : "excluded", view);
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
            ctx.refsGroup.add(mesh);
            ctx.refMeshById.set(ref.id, mesh);
        }
    }
    ctx.refsGroup.visible = st.cellsVisible;
    refreshRefStyles(ctx);
};

/** Recolour the refs for the selection: the selected one fuller, its edges white. */
export const refreshRefStyles = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const sel = st.selection;
    for (const [refId, mesh] of ctx.refMeshById) {
        const ref = st.refs[refId];
        if (!ref) continue;
        const selected = sel?.kind === "ref" && sel.refId === refId;
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
