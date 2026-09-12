/**
 * Hover / selection OVERLAYS and face styling.
 *
 * Owns: the two fat-line edge overlays (hover + selected), the always-on-top
 * quad drawn over the selected box face, and the one routine that recomputes
 * every box face's colour and opacity from base + selection + hover state.
 * Also the shared mesh-disposal helper.
 */

import * as THREE from "three";
import {LineSegments2} from "three/examples/jsm/lines/LineSegments2";
import {LineSegmentsGeometry} from "three/examples/jsm/lines/LineSegmentsGeometry";
import {LineMaterial} from "three/examples/jsm/lines/LineMaterial";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {requestRender} from "@/state/perfStore";
import {BOX_FACE_SIDES, edgeEndpoints, type EdgeHit, type Vec3} from "@/utils/cellbuilder/snap";
import {BASE_OPACITY, EXCLUDED_FACE_COLOR, HOVER_FACE_COLOR, LOFT_OPACITY, SELECTED_FACE_COLOR, colorForKind} from "./sceneConstants";
import type {CellBuilderScene} from "./sceneContext";

// Fat-line overlays for edge hover/selection (thickness in pixels; a plain
// LineBasicMaterial's linewidth is ignored by WebGL).
export const placeEdgeOverlay = (
    ctx: CellBuilderScene,
    line: LineSegments2,
    cellId: string,
    faceIndex: number,
    edge: EdgeHit,
): boolean => {
    const cell = useCellBuilderStore.getState().cells[cellId];
    if (!cell) return false;
    const {start, end} = edgeEndpoints(cell, faceIndex, edge);
    line.geometry.dispose();
    const geo = new LineSegmentsGeometry();
    geo.setPositions([...start, ...end]);
    line.geometry = geo;
    const size = ctx.renderer.getSize(new THREE.Vector2());
    (line.material as LineMaterial).resolution.set(size.x, size.y);
    return true;
};

export const refreshEdgeOverlays = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const sel = st.selection;
    ctx.selectedEdgeLine.visible =
        sel?.kind === "edge" && sel.faceIndex !== undefined && sel.edge !== undefined && st.cellsVisible
            ? placeEdgeOverlay(ctx, ctx.selectedEdgeLine, sel.cellId, sel.faceIndex, sel.edge)
            : false;
    const hoverIsSelected =
        ctx.hoveredEdge !== null &&
        sel?.kind === "edge" &&
        sel.cellId === ctx.hoveredEdge.cellId &&
        sel.faceIndex === ctx.hoveredEdge.faceIndex &&
        sel.edge?.axis === ctx.hoveredEdge.edge.axis &&
        sel.edge?.boundaryAxis === ctx.hoveredEdge.edge.boundaryAxis &&
        sel.edge?.boundaryPositive === ctx.hoveredEdge.edge.boundaryPositive;
    ctx.hoverEdgeLine.visible =
        ctx.hoveredEdge !== null && !hoverIsSelected && st.cellsVisible
            ? placeEdgeOverlay(ctx, ctx.hoverEdgeLine, ctx.hoveredEdge.cellId, ctx.hoveredEdge.faceIndex, ctx.hoveredEdge.edge)
            : false;
};

export const disposeMesh = (ctx: CellBuilderScene, m: THREE.Mesh) => {
    m.geometry.dispose();
    const mats = Array.isArray(m.material) ? m.material : [m.material];
    mats.forEach((x) => x.dispose());
};

// Always-on-top overlay quad for the SELECTED box-cell face: a bright fill
// drawn with depthTest off + a high renderOrder, so the picked face is fully
// visible even THROUGH the cell body (a material tint on the shared box mesh
// can't reliably beat its own near faces' draw order — hence a separate mesh).
const FACE_OVERLAY_IDX = new Uint16Array([0, 1, 2, 0, 2, 3]);
// Position/size the overlay on the selected box face (model space), or hide.
export const updateFaceOverlay = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const sel = st.selection;
    const cell = sel ? st.cells[sel.cellId] : null;
    if (
        !cell ||
        cell.kind !== "cell" ||
        sel?.kind !== "face" ||
        sel.faceIndex == null ||
        !BOX_FACE_SIDES[sel.faceIndex] ||
        !st.cellsVisible
    ) {
        if (ctx.faceOverlay.visible) ctx.faceOverlay.visible = false;
        return;
    }
    const side = BOX_FACE_SIDES[sel.faceIndex];
    const [a1, a2] = ([0, 1, 2] as const).filter((a) => a !== side.axis) as [
        0 | 1 | 2,
        0 | 1 | 2,
    ];
    const base: Vec3 = [...cell.origin];
    if (side.positive) base[side.axis] += cell.size[side.axis];
    const c0: Vec3 = [...base];
    const c1: Vec3 = [...base];
    c1[a1] += cell.size[a1];
    const c2: Vec3 = [...c1];
    c2[a2] += cell.size[a2];
    const c3: Vec3 = [...base];
    c3[a2] += cell.size[a2];
    const pos = new Float32Array([...c0, ...c1, ...c2, ...c3]);
    ctx.faceOverlay.geometry.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    ctx.faceOverlay.geometry.setIndex(new THREE.BufferAttribute(FACE_OVERLAY_IDX, 1));
    ctx.faceOverlay.geometry.computeBoundingSphere();
    ctx.faceOverlay.visible = true;
};

// Recompute every face material's color/opacity from base + selection +
// hover state. Cheap (6 materials per box) and keeps one source of truth.
export const refreshFaceStyles = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const sel = st.selection;
    // Every cell in the multi-select set highlights (not just the primary),
    // so an add-mode selection shows what a Hide / copy will act on.
    const selectedSet = new Set(st.selectedCellIds);
    for (const [cellId, mesh] of ctx.meshById) {
        const cell = st.cells[cellId];
        if (!cell) continue;
        const base = colorForKind(cell.kind);
        const cellSelected = sel?.cellId === cellId || selectedSet.has(cellId);
        // Loft band (Phase 3b): per-face pickable when built with material
        // groups (one per side panel + cap) — highlight the picked panel and
        // dim excluded ones. A mismatched-count band has a single material
        // (whole-cell select/hover only). Either way the ring overlay tracks
        // selection.
        if (cell.kind === "loft") {
            const excluded = new Set(cell.loft?.excludeFaces ?? []);
            const faceIds = mesh.userData.__loftFaceIds as string[] | undefined;
            if (Array.isArray(mesh.material) && faceIds) {
                const mats = mesh.material as THREE.MeshBasicMaterial[];
                let hasThroughFace = false;
                for (let fi = 0; fi < mats.length; fi++) {
                    const isExcluded = excluded.has(faceIds[fi]);
                    let color = base;
                    let opacity = cellSelected ? 0.5 : LOFT_OPACITY;
                    let through = false;
                    if (cellSelected && sel?.kind === "face" && sel.faceIndex === fi) {
                        color = SELECTED_FACE_COLOR;
                        opacity = 0.7;
                        through = true;
                    }
                    if (ctx.hovered?.mesh === mesh && ctx.hovered.faceIndex === fi) {
                        color = HOVER_FACE_COLOR;
                        opacity = 0.6;
                    }
                    // Removed panel: grey wireframe, barely visible — but keep
                    // the pick/hover tint so the selected excluded face still
                    // reads (its panel row is highlighted in the info panel).
                    if (isExcluded) {
                        if (color === base) color = EXCLUDED_FACE_COLOR;
                        opacity = 0.12;
                    }
                    mats[fi].wireframe = isExcluded;
                    mats[fi].color.setHex(color);
                    mats[fi].opacity = opacity;
                    mats[fi].depthTest = !through; // selected face draws through
                    if (through) hasThroughFace = true;
                }
                mesh.renderOrder = hasThroughFace ? 3 : 0;
            } else {
                const m = mesh.material as THREE.MeshBasicMaterial;
                let color = base;
                let opacity = cellSelected ? 0.5 : LOFT_OPACITY;
                if (ctx.hovered?.mesh === mesh) {
                    color = HOVER_FACE_COLOR;
                    opacity = 0.6;
                }
                m.color.setHex(color);
                m.opacity = opacity;
            }
            const loftEdges = mesh.children[0] as THREE.LineSegments | undefined;
            if (loftEdges) {
                (loftEdges.material as THREE.LineBasicMaterial).color.setHex(
                    cellSelected ? 0xffffff : base,
                );
            }
            continue;
        }
        const mats = mesh.material as THREE.MeshBasicMaterial[];
        for (let fi = 0; fi < mats.length; fi++) {
            let color = base;
            let opacity = BASE_OPACITY;
            if (cellSelected) opacity = 0.4;
            // The selected face is tinted here (front-facing) AND drawn as an
            // always-on-top overlay quad (faceOverlay) so it's fully visible
            // even through the cell body — see updateFaceOverlay.
            if (cellSelected && sel?.kind === "face" && sel.faceIndex === fi) {
                color = SELECTED_FACE_COLOR;
                opacity = 0.55;
            }
            if (ctx.hovered?.mesh === mesh && ctx.hovered.faceIndex === fi) {
                color = HOVER_FACE_COLOR;
                opacity = 0.6;
            }
            mats[fi].color.setHex(color);
            mats[fi].opacity = opacity;
        }
        const edgeLines = mesh.children[0] as THREE.LineSegments | undefined;
        if (edgeLines) {
            (edgeLines.material as THREE.LineBasicMaterial).color.setHex(cellSelected ? 0xffffff : base);
        }
    }
    refreshEdgeOverlays(ctx);
    updateFaceOverlay(ctx);
    requestRender();
};
