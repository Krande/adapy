/**
 * Builder GRID.
 *
 * Owns: swapping the scene's static helper grid for one whose line spacing IS
 * the snap step while a procedural model is open, and disposing it again. The
 * grid lives inside the builder container, so its intersections are exactly the
 * model-space points the snap quantises to.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import type {CellBuilderScene} from "./sceneContext";

// While a procedural model is open, the scene's static 1 m helper grid is
// swapped for a builder grid whose line spacing IS the snap gridStep (and
// which lives inside the container, so its intersections are exactly the
// model-space points quantize() snaps to).
const GRID_TARGET_EXTENT = 60; // meters; divisions derive from gridStep
const GRID_MAX_DIVISIONS = 2000;
export const disposeBuilderGrid = (ctx: CellBuilderScene) => {
    if (!ctx.builderGrid) return;
    ctx.builderGrid.geometry.dispose();
    (ctx.builderGrid.material as THREE.Material).dispose();
    ctx.container.remove(ctx.builderGrid);
    ctx.builderGrid = null;
    ctx.builderGridStep = -1;
};

export const syncBuilderGrid = (ctx: CellBuilderScene) => {
    const st = useCellBuilderStore.getState();
    const wantGrid = st.active !== null && st.gridStep > 0;

    // Toggle the default scene grid(s) opposite to ours.
    if (wantGrid && ctx.hiddenDefaultGrids.length === 0) {
        for (const o of ctx.scene.children) {
            if (o instanceof THREE.GridHelper && o !== ctx.builderGrid && o.visible) {
                o.visible = false;
                ctx.hiddenDefaultGrids.push(o);
            }
        }
    } else if (!wantGrid && ctx.hiddenDefaultGrids.length > 0) {
        ctx.hiddenDefaultGrids.forEach((g) => (g.visible = true));
        ctx.hiddenDefaultGrids.length = 0;
    }

    if (!wantGrid) {
        disposeBuilderGrid(ctx);
        requestRender();
        return;
    }
    if (ctx.builderGrid && ctx.builderGridStep === st.gridStep) return;

    disposeBuilderGrid(ctx);
    // Even division count so the centered grid's lines land exactly on
    // n * gridStep (extent/2 must itself be a multiple of gridStep).
    const half = Math.min(GRID_MAX_DIVISIONS / 2, Math.max(1, Math.round(GRID_TARGET_EXTENT / (2 * st.gridStep))));
    const divisions = 2 * half;
    const extent = divisions * st.gridStep;
    ctx.builderGrid = new THREE.GridHelper(extent, divisions, 0x6b7280, 0x374151);
    (ctx.builderGrid.material as THREE.Material).depthWrite = false;
    ctx.builderGrid.renderOrder = -1;
    ctx.builderGrid.layers.set(1);
    if (useModelState.getState().zIsUp) {
        ctx.builderGrid.rotation.x = Math.PI / 2; // XZ default -> model XY plane
    }
    ctx.builderGridStep = st.gridStep;
    ctx.container.add(ctx.builderGrid);
    requestRender();
};
