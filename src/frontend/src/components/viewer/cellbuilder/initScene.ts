/**
 * Builder scene COMPOSER.
 *
 * Owns: standing the whole builder up for one viewer — create the scene
 * context, wire the gizmo events, register the DOM listeners, subscribe to the
 * three stores that drive a redraw, and hand back the teardown that undoes all
 * of it. The only place the modules are strung together; each of them takes the
 * context explicitly, so nothing here closes over React scope.
 */

import * as THREE from "three";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useCompanionModelStore} from "@/state/companionModelStore";
import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {createCellBuilderScene, syncOffset} from "./sceneContext";
import {disposeBuilderGrid, syncBuilderGrid} from "./builderGrid";
import {disposeMesh, refreshEdgeOverlays, refreshFaceStyles} from "./overlays";
import {applyCellVisibility, rebuild, rebuildCompanions} from "./meshRebuild";
import {rebuildCadPreviews} from "./cadPreview";
import {clearPorts, rebuildPorts} from "./portsOverlay";
import {installCellGizmo} from "./cellGizmo";
import {installPortGizmo, syncPortGizmo} from "./portGizmo";
import {disposeResizeHandles, syncGizmo} from "./resizeHandles";
import {clearLongPress, onContextMenu, onPointerCancel, onPointerDown, onPointerMove, onPointerUp} from "./pointerTools";
import {endPlaceEntry} from "./numericEntry";
import {endEquipEntry, endOpenEntry} from "./insertTools";
import {onKeyDown} from "./keyboard";

export function initCellBuilderScene(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.Camera,
): () => void {
    const ctx = createCellBuilderScene(renderer, scene, camera);
    syncOffset(ctx);
    installCellGizmo(ctx);
    installPortGizmo(ctx);

    // Stable handler identities, so add/removeEventListener pair up.
    const hPointerDown = (ev: PointerEvent) => onPointerDown(ctx, ev);
    const hPointerMove = (ev: PointerEvent) => onPointerMove(ctx, ev);
    const hPointerUp = (ev: PointerEvent) => onPointerUp(ctx, ev);
    const hPointerCancel = (ev: PointerEvent) => onPointerCancel(ctx, ev);
    const hContextMenu = (ev: MouseEvent) => onContextMenu(ctx, ev);
    const hKeyDown = (ev: KeyboardEvent) => onKeyDown(ctx, ev);

    const el = ctx.renderer.domElement;
    // Capture phase so a grab on a builder face wins over the scene's own
    // click-selection/orbit-pivot handlers.
    el.addEventListener("pointerdown", hPointerDown, true);
    el.addEventListener("pointermove", hPointerMove, true);
    el.addEventListener("pointerup", hPointerUp, true);
    el.addEventListener("pointercancel", hPointerCancel, true);
    el.addEventListener("contextmenu", hContextMenu);
    // Capture phase so the builder's Shift+H / gizmo keys can preempt (and
    // stopPropagation) the global viewer key handler, which listens on bubble.
    window.addEventListener("keydown", hKeyDown, true);

    rebuild(ctx);
    rebuildPorts(ctx);
    syncBuilderGrid(ctx);
    syncGizmo(ctx);
    syncPortGizmo(ctx);
    rebuildCompanions(ctx);
    // Its own subscription, so a companion change never re-runs the editable
    // rebuild (which clears hover, selection and the mesh index).
    const unsubCompanions = useCompanionModelStore.subscribe((s, prev) => {
        if (s.companions !== prev.companions) rebuildCompanions(ctx);
    });

    const unsub = useCellBuilderStore.subscribe((s, prev) => {
        // Leaving an add mode drops any in-progress numeric placement.
        if (
            ctx.placeEntry &&
            s.mode !== prev.mode &&
            !(s.mode === "add-cell" || s.mode === "add-opening" || s.mode === "add-equipment")
        ) {
            endPlaceEntry(ctx);
        }
        // Drop the keyboard equipment / opening flows if the model closed or the
        // cell they target vanished (e.g. deleted / undone underneath them).
        if (ctx.equipEntry && (!s.active || !s.cells[ctx.equipEntry.hostId])) endEquipEntry(ctx);
        if (ctx.openEntry && (!s.active || !s.cells[ctx.openEntry.cellId])) endOpenEntry(ctx);
        // equipmentCad flips whether equipment boxes fit their CAD or fall back
        // to LX/LY/LZ, so refit on toggle.
        if (s.cells !== prev.cells || s.active !== prev.active || s.equipmentCad !== prev.equipmentCad) rebuild(ctx);
        else if (s.selection !== prev.selection || s.selectedCellIds !== prev.selectedCellIds)
            refreshFaceStyles(ctx);
        // "Show as CAD" toggles + any cell/type change re-seat the CAD previews
        // (after rebuild() has re-made the boxes so applyCellVisibility hides the
        // right ones).
        if (
            s.cadPreviewCells !== prev.cadPreviewCells ||
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.equipmentTypes !== prev.equipmentTypes
        )
            rebuildCadPreviews(ctx);
        // Keep the keyboard "active loft station" in step with the selection:
        // reset it when the selected loft MEMBER changes (preserving the index
        // within the same member, so F/D + setLoftActive don't fight), and clear
        // it when the pick isn't a loft band.
        if (s.selection !== prev.selection) {
            const sc = s.selection ? s.cells[s.selection.cellId] : null;
            if (sc && sc.kind === "loft" && sc.loft) {
                if (!ctx.loftActive || ctx.loftActive.member !== sc.loft.member)
                    ctx.loftActive = {member: sc.loft.member, index: sc.loft.bay};
            } else {
                ctx.loftActive = null;
            }
        }
        if (
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.portsOverlayVisible !== prev.portsOverlayVisible ||
            s.equipmentTypes !== prev.equipmentTypes ||
            s.systems !== prev.systems
        ) {
            rebuildPorts(ctx);
        }
        if (
            s.selection !== prev.selection ||
            s.gizmoMode !== prev.gizmoMode ||
            s.gizmoAxisLock !== prev.gizmoAxisLock ||
            s.gizmoVertexSnap !== prev.gizmoVertexSnap ||
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.gridStep !== prev.gridStep ||
            s.cellsVisible !== prev.cellsVisible
        ) {
            syncGizmo(ctx);
        }
        if (
            s.portGizmo !== prev.portGizmo ||
            s.cells !== prev.cells ||
            s.active !== prev.active ||
            s.portsOverlayVisible !== prev.portsOverlayVisible ||
            s.cellsVisible !== prev.cellsVisible ||
            s.gizmoVertexSnap !== prev.gizmoVertexSnap ||
            s.gridStep !== prev.gridStep
        ) {
            syncPortGizmo(ctx);
        }
        if (s.active !== prev.active || s.gridStep !== prev.gridStep) syncBuilderGrid(ctx);
        if (s.cellsVisible !== prev.cellsVisible) {
            ctx.cellsGroup.visible = s.cellsVisible;
            ctx.cadPreviewGroup.visible = s.cellsVisible;
            refreshEdgeOverlays(ctx);
            requestRender();
        }
        if (s.hiddenCellIds !== prev.hiddenCellIds) {
            applyCellVisibility(ctx);
            requestRender();
        }
        if (
            s.mode !== prev.mode &&
            s.mode !== "add-cell" &&
            s.mode !== "add-equipment" &&
            s.mode !== "add-opening"
        ) {
            ctx.ghost.visible = false;
            ctx.ghostBox = null;
            requestRender();
        }
    });
    const unsubModel = useModelState.subscribe((s, prev) => {
        if (s.translation !== prev.translation) syncOffset(ctx);
        // A model appearing/disappearing (e.g. the compiled CAD GLB overlay)
        // changes which equipment meshes are in the scene; refit CAD-backed
        // equipment boxes to the newly available geometry. Deferred a frame so
        // the freshly added meshes' world matrices are settled before we read
        // their bounds.
        if (s.loadedSourceNames !== prev.loadedSourceNames && useCellBuilderStore.getState().equipmentCad) {
            requestAnimationFrame(() => rebuild(ctx));
        }
    });

    return () => {
        unsub();
        unsubCompanions();
        unsubModel();
        el.removeEventListener("pointerdown", hPointerDown, true);
        el.removeEventListener("pointermove", hPointerMove, true);
        el.removeEventListener("pointerup", hPointerUp, true);
        el.removeEventListener("pointercancel", hPointerCancel, true);
        el.removeEventListener("contextmenu", hContextMenu);
        window.removeEventListener("keydown", hKeyDown, true);
        const runtimeCtl = getViewerRuntime().controls.current;
        if (runtimeCtl) runtimeCtl.enabled = true;
        clearLongPress(ctx);
        ctx.gizmo.detach();
        ctx.gizmo.dispose();
        ctx.scene.remove(ctx.gizmoHelper);
        ctx.portGizmo.detach();
        ctx.portGizmo.dispose();
        ctx.scene.remove(ctx.portGizmoHelper);
        ctx.guideLine.geometry.dispose();
        (ctx.guideLine.material as THREE.Material).dispose();
        ctx.container.remove(ctx.guideLine);
        ctx.snapTex.dispose();
        (ctx.snapMarker.material as THREE.Material).dispose();
        ctx.scene.remove(ctx.snapMarker);
        ctx.readoutTex.dispose();
        (ctx.readout.material as THREE.Material).dispose();
        ctx.scene.remove(ctx.readout);
        ctx.ringPreview.geometry.dispose();
        (ctx.ringPreview.material as THREE.Material).dispose();
        ctx.container.remove(ctx.ringPreview);
        ctx.faceOverlay.geometry.dispose();
        (ctx.faceOverlay.material as THREE.Material).dispose();
        ctx.container.remove(ctx.faceOverlay);
        disposeResizeHandles(ctx);
        ctx.hiddenDefaultGrids.forEach((g) => (g.visible = true));
        ctx.hiddenDefaultGrids.length = 0;
        disposeBuilderGrid(ctx);
        clearPorts(ctx);
        // Free the CAD-preview cache prototypes (the scene clones share their
        // resources, so this is the single owner that disposes them).
        for (const proto of ctx.cadPreviewCache.values()) {
            if (proto === "loading" || proto === "error") continue;
            proto.traverse((m: any) => {
                if (m.isMesh || m.isLineSegments) disposeMesh(ctx, m);
            });
        }
        ctx.cadPreviewCache.clear();
        ctx.portMarkerGeom.dispose();
        for (let i = ctx.container.children.length - 1; i >= 0; i--) {
            const o = ctx.container.children[i];
            o.traverse((m: any) => {
                if (m.isMesh || m.isLineSegments) disposeMesh(ctx, m);
            });
            ctx.container.remove(o);
        }
        ctx.scene.remove(ctx.container);
        requestRender();
    };
}
