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
import {useGroupViewStore} from "@/state/cellbuilder/groupViewStore";
import {useIncludeViewStore} from "@/state/includeViewStore";
import {useModelState} from "@/state/modelState";
import {requestRender} from "@/state/perfStore";
import type {BuilderViewHost} from "./viewHost";
import {createCellBuilderScene, syncOffset} from "./sceneContext";
import {disposeBuilderGrid, syncBuilderGrid} from "./builderGrid";
import {disposeMesh, refreshEdgeOverlays, refreshFaceStyles} from "./overlays";
import {applyCellVisibility, rebuild, rebuildCompanions} from "./meshRebuild";
import {disposeRefs, rebuildRefs, refreshRefStyles} from "./refsOverlay";
import {rebuildCadPreviews} from "./cadPreview";
import {clearPorts, rebuildPorts} from "./portsOverlay";
import {installCellGizmo} from "./cellGizmo";
import {installPortGizmo, syncPortGizmo} from "./portGizmo";
import {disposeResizeHandles, keepHandlesScreenSized, syncGizmo, translationSnapFor} from "./resizeHandles";
import {installFreeMoveKeys, onFreeMoveChange} from "@/utils/gizmo/freeMove";
import {keepScreenSized} from "@/utils/gizmo/screenSize";
import {disposeCellDimensions, disposeMoveMeasure} from "./cellDimensions";
import {clearLongPress, onContextMenu, onPointerCancel, onPointerDown, onPointerMove, onPointerUp} from "./pointerTools";
import {itemIncludeState} from "@/utils/cellbuilder/include";

type BuilderState = ReturnType<typeof useCellBuilderStore.getState>;

/** Every cell's include state, as one string: what a groups or loft-members change may alter in the drawing. */
const includeStates = (s: BuilderState): string =>
    Object.values(s.cells)
        .map((c) => `${c.id}:${itemIncludeState(c, s.cells, s.groups, s.loftMembers)}`)
        .join("\n");
import {endPlaceEntry} from "./numericEntry";
import {endEquipEntry, endOpenEntry} from "./insertTools";
import {onKeyDown} from "./keyboard";
import {openedModelFramer} from "./frameOpened";
import {shownInScene} from "./cellBoxSelect";
import {setBuilderVisibilityHost} from "@/state/cellbuilder/builderVisibility";
import {disposeInfoLabel, syncInfoLabel} from "./infoLabel";

export function initCellBuilderScene(host: BuilderViewHost): () => void {
    const ctx = createCellBuilderScene(host);
    syncOffset(ctx);
    installCellGizmo(ctx);
    installPortGizmo(ctx);
    // The gizmos and the resize handles stay one size on screen, in perspective and orthographic.
    const unsize = [keepScreenSized(ctx.gizmo), keepScreenSized(ctx.portGizmo), keepHandlesScreenSized(ctx)];
    // Left Shift held while dragging: free move. The widgets' own step follows it at once, mid-drag.
    const releaseFreeKeys = installFreeMoveKeys();
    const unsubFreeMove = onFreeMoveChange(() => {
        const st = useCellBuilderStore.getState();
        if (ctx.gizmo.mode === "translate") ctx.gizmo.setTranslationSnap(translationSnapFor(st));
        if (ctx.portGizmo.mode === "translate") ctx.portGizmo.setTranslationSnap(translationSnapFor(st));
    });

    // The builder takes the pointer and its keys only while its cells are drawn. A shell may keep
    // the procedural model open behind a mode that hides them (the docked UI's Output without a
    // comparison, its FEM modes): there a click, a right-click and Ctrl+Z / Shift+H / Delete / G
    // ... are the viewer's, not the hidden cells' (a Delete left to the builder deleted the cell
    // selected back in Input). What a press or a key started (a drag, a typed entry) still gets
    // its keys and moves, and a release and a cancel always reach it, to end it.
    const shown = () => shownInScene(ctx.cellsGroup);
    // The hide / show commands act on the selected cells while they are drawn (state/cellbuilder/builderVisibility).
    const unprobe = setBuilderVisibilityHost({
        cellsShown: () => useCellBuilderStore.getState().active !== null && shown(),
        selected: () => {
            const st = useCellBuilderStore.getState();
            return st.selectedCellIds.length ? [...st.selectedCellIds] : st.selection?.cellId ? [st.selection.cellId] : [];
        },
        all: () => Object.keys(useCellBuilderStore.getState().cells),
        hide: (ids) => useCellBuilderStore.getState().hideCells([...ids]),
        unhide: (ids) => useCellBuilderStore.getState().unhideCells([...ids]),
        unhideAll: () => useCellBuilderStore.getState().unhideAllCells(),
    });
    const busy = () =>
        ctx.drag !== null || ctx.modalMove !== null || ctx.numEntry !== null || ctx.placeEntry !== null || ctx.equipEntry !== null || ctx.openEntry !== null;
    // Stable handler identities, so add/removeEventListener pair up.
    const hPointerDown = (ev: PointerEvent) => void (shown() && onPointerDown(ctx, ev));
    const hPointerMove = (ev: PointerEvent) => void ((shown() || busy()) && onPointerMove(ctx, ev));
    const hPointerUp = (ev: PointerEvent) => onPointerUp(ctx, ev);
    const hPointerCancel = (ev: PointerEvent) => onPointerCancel(ctx, ev);
    const hContextMenu = (ev: MouseEvent) => {
        if (!shown()) return;
        onContextMenu(ctx, ev);
        // A menu the builder opened stops the event: the scene's own menus (whether on an element
        // above this one or on this one, as the three.js-free viewport's are) stay shut.
        if (ev.cancelBubble) ev.stopImmediatePropagation();
    };
    const hKeyDown = (ev: KeyboardEvent) => void ((shown() || busy()) && onKeyDown(ctx, ev));

    const el = ctx.host.input();
    // Capture phase so a grab on a builder face wins over the scene's own
    // click-selection/orbit-pivot handlers.
    el.addEventListener("pointerdown", hPointerDown, true);
    el.addEventListener("pointermove", hPointerMove, true);
    el.addEventListener("pointerup", hPointerUp, true);
    el.addEventListener("pointercancel", hPointerCancel, true);
    el.addEventListener("contextmenu", hContextMenu, true);
    // Capture phase so the builder's Shift+H / gizmo keys can preempt (and
    // stopPropagation) the global viewer key handler, which listens on bubble.
    window.addEventListener("keydown", hKeyDown, true);

    rebuild(ctx);
    rebuildRefs(ctx);
    const framer = openedModelFramer(ctx.cellsGroup, () => useCellBuilderStore.getState().active?.modelId ?? null);
    framer.check();
    rebuildPorts(ctx);
    syncBuilderGrid(ctx);
    syncGizmo(ctx);
    syncPortGizmo(ctx);
    syncInfoLabel(ctx);
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
        if (s.cells !== prev.cells || s.active !== prev.active || s.equipmentCad !== prev.equipmentCad) {
            rebuild(ctx);
            framer.check();
        } else if (
            (s.groups !== prev.groups || s.loftMembers !== prev.loftMembers) &&
            includeStates(s) !== includeStates(prev)
        ) {
            // A structure's INCLUDE (its group) or a loft member's changes what is drawn as left
            // out. Only then: `groups` is replaced on every blueprint edit too, and a rebuild
            // remakes every mesh.
            rebuild(ctx);
        } else if (s.selection !== prev.selection || s.selectedCellIds !== prev.selectedCellIds)
            refreshFaceStyles(ctx);
        // The plugins' refs: drawn again when they or the model change, restyled on a new pick.
        if (s.refs !== prev.refs || s.active !== prev.active) rebuildRefs(ctx);
        else if (s.selection !== prev.selection) refreshRefStyles(ctx);
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
            const sc = s.selection?.cellId ? s.cells[s.selection.cellId] : null;
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
            s.refs !== prev.refs ||
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
        // The info label follows the pick, its edits and moves, and goes with the flag or the cells.
        if (
            s.infoLabel !== prev.infoLabel ||
            s.selection !== prev.selection ||
            s.cells !== prev.cells ||
            s.refs !== prev.refs ||
            s.active !== prev.active ||
            s.cellsVisible !== prev.cellsVisible
        )
            syncInfoLabel(ctx);
        if (s.cellsVisible !== prev.cellsVisible) {
            ctx.cellsGroup.visible = s.cellsVisible;
            ctx.refsGroup.visible = s.cellsVisible;
            ctx.cadPreviewGroup.visible = s.cellsVisible;
            refreshEdgeOverlays(ctx);
            requestRender();
        }
        if (s.hiddenCellIds !== prev.hiddenCellIds) {
            applyCellVisibility(ctx);
            requestRender();
        }
        // The cell colours follow the groups: a blueprint change recolours its structure,
        // and colour by group re-palettes on a rename / add / remove.
        if (s.groups !== prev.groups) refreshFaceStyles(ctx);
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
    // Colour by group (Groups section) restyles the boxes; it is view state kept
    // outside the cellbuilder store, so it needs its own subscription.
    // The include view (what the build leaves out: dimmed, hidden or drawn like the rest).
    const unsubIncludeView = useIncludeViewStore.subscribe((s, prev) => {
        if (s.view !== prev.view) {
            rebuild(ctx);
            rebuildRefs(ctx);
        }
    });
    const unsubGroupView = useGroupViewStore.subscribe((s, prev) => {
        if (s.colourByGroup !== prev.colourByGroup || s.colours !== prev.colours) refreshFaceStyles(ctx);
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
    // What the viewport on screen needs of the builder's scene (./viewHost.ts): nothing on the
    // three.js viewport, which draws it; the three.js-free one draws it through its overlays.
    const unbindHost = ctx.host.bind?.(ctx) ?? (() => {});

    return () => {
        framer.dispose();
        unsub();
        unsubCompanions();
        unsubGroupView();
        unsubIncludeView();
        unsubModel();
        unsubFreeMove();
        releaseFreeKeys();
        for (const undo of unsize) undo();
        el.removeEventListener("pointerdown", hPointerDown, true);
        el.removeEventListener("pointermove", hPointerMove, true);
        el.removeEventListener("pointerup", hPointerUp, true);
        el.removeEventListener("pointercancel", hPointerCancel, true);
        el.removeEventListener("contextmenu", hContextMenu, true);
        unprobe();
        window.removeEventListener("keydown", hKeyDown, true);
        unbindHost();
        ctx.host.setCameraEnabled(true);
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
        disposeRefs(ctx);
        disposeCellDimensions(ctx);
        ctx.container.remove(ctx.dimensionGroup);
        disposeInfoLabel(ctx);
        disposeMoveMeasure(ctx);
        ctx.container.remove(ctx.moveMeasureGroup);
        ctx.host.setGroundGridHidden(false);
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
