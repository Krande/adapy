import * as THREE from "three";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {centerViewOnSelection} from "@/utils/scene/centerViewOnSelection";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {useOptionsStore} from "@/state/optionsStore";
import {useGalleryStore} from "@/state/galleryStore";
import {useMeStore} from "@/state/meStore";
import {useViewMetricsStore} from "@/state/viewMetricsStore";
import {useViewerPanelStore} from "@/state/viewerPanelStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import CameraControls from "camera-controls";
import {copySelectionNames} from "@/utils/clipboard/copySelectionNames";
import {toggleTreeFocus} from "@/utils/tree_view/treeKeyboard";
import {groupSelectionFromKeyboard} from "@/utils/groups/groupShortcut";
import {hideSelectedRanges, hideUnselectedRanges, unhideAllRanges} from "@/utils/scene/visibility";
import {applyAdaptiveClipping} from "@/components/viewer/sceneHelpers/adaptiveClipping";
import {frameBox, zoomToAll} from "@/components/viewer/sceneHelpers/cameraFit";

// The camera fits live in ./cameraFit (light enough for the plugin verbs and tests to import);
// re-exported here, where their callers have always found them.
export {frameBox, zoomToAll};
import {selectChildLevel, selectParentLevel, selectSibling} from "@/utils/tree_view/treeNavigation";
import {useCellBuilderStore, needsPreviewCompile} from "@/state/cellBuilderStore";
import {frameCells} from "@/utils/scene/frameCells";
import {requestRender} from "@/state/perfStore";
import {useModelState} from "@/state/modelState";
import {undoRedoIntent} from "@/utils/keyboard/undoRedoKeys";
import {redoViewerAction, undoViewerAction} from "@/utils/scene/viewerHistory";
import {bindViewerHistoryReset} from "@/utils/scene/viewerHistoryBindings";

export function setupCameraControlsHandlers(
    scene: THREE.Scene,
    camera: THREE.PerspectiveCamera,
    controls: CameraControls | OrbitControls,
    /**
     * Optional scope element. When provided, the Shift+key shortcuts
     * only fire while the pointer is over (or keyboard focus is
     * inside) this element. Without a scope, behaves as before:
     * window-global, fires from anywhere on the page.
     *
     * The standalone viewer (full-page app) passes nothing — the
     * whole tab is the viewer, so global scope is correct. The
     * paradoc embed passes its mount element so the shortcuts don't
     * leak into the host page (Shift+T would otherwise toggle the
     * adapy tree while the reader user was typing in a paradoc
     * search box, etc.).
     */
    scopeEl?: HTMLElement,
) {
    // Treat the listener as in-scope by default when no element is
    // given (preserves the standalone's global behavior). Otherwise
    // track pointer-over + focus-inside on the scope element.
    let inScope = !scopeEl;
    let onEnter: (() => void) | null = null;
    let onLeave: (() => void) | null = null;
    let onFocusIn: (() => void) | null = null;
    let onFocusOut: ((e: FocusEvent) => void) | null = null;
    if (scopeEl) {
        onEnter = () => { inScope = true; };
        onLeave = () => { inScope = false; };
        onFocusIn = () => { inScope = true; };
        onFocusOut = (e) => {
            // Focus moved out of the scope subtree → leave scope. But
            // related target may be null on tab-out; treat that as out.
            const next = e.relatedTarget as Node | null;
            if (!next || !scopeEl.contains(next)) inScope = false;
        };
        scopeEl.addEventListener("mouseenter", onEnter);
        scopeEl.addEventListener("mouseleave", onLeave);
        scopeEl.addEventListener("focusin", onFocusIn);
        scopeEl.addEventListener("focusout", onFocusOut as EventListener);
    }

    const handleKeyDown = (event: KeyboardEvent) => {
        if (!inScope) return;

        // Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z, Cmd on macOS): step the viewer's hide / show / selection
        // history. undoRedoIntent leaves a text field's own undo alone; an open procedural cell
        // builder has its own history and takes the keys first.
        const intent = undoRedoIntent(event);
        if (intent) {
            if (useCellBuilderStore.getState().active !== null) return;
            event.preventDefault();
            if (intent === "undo") undoViewerAction();
            else redoViewerAction();
            return;
        }


        // Don't hijack shortcuts while the user is typing in a form
        // field — same trap browsers use for `/` etc. The standalone
        // never had this check either, but the embed amplifies the
        // problem (paradoc has search boxes a click away).
        const t = event.target as HTMLElement | null;
        if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) {
            return;
        }

        const key = event.key.toLowerCase();
        const shift = event.shiftKey;
        const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
        const ctrl = event.ctrlKey || event.metaKey;

        if (ctrl && !shift && !event.altKey && key === "c") {
            // Ctrl/Cmd+C copies the selection's names -- the levels' own names when levels are
            // picked in the tree -- unless the user has highlighted text, which the browser copies.
            if ((window.getSelection()?.toString() ?? "").length > 0) return;
            if (selectedObjects.size === 0 && !useTreeViewStore.getState().tree?.selectedNodes.length) return;
            event.preventDefault();
            void copySelectionNames(selectedObjects).then((n) => {
                if (n === 0) console.warn("Ctrl+C: nothing copied");
            });
            return;
        }

        if (shift && key === "h") {
            hideSelectedRanges();
        } else if (shift && key === "i" && !event.ctrlKey && !event.metaKey && !event.altKey) {
            // Modifiers excluded so a browser chord (Ctrl+Shift+I opens devtools) never also
            // hides half the model.
            hideUnselectedRanges();
        } else if (shift && key === "g" && !ctrl && !event.altKey) {
            // Shift+G: save the selection as a named group shared in the scope.
            event.preventDefault();
            void groupSelectionFromKeyboard();
        } else if (shift && key === "u") {
            unhideAllRanges();
        } else if (shift && key === "f") {
            fitSelectionView(camera, controls);
        } else if (shift && key === "a") {
            fitAllView(scene, camera, controls);
        } else if (shift && key === "enter") {
            // ⇧↵ — preview-compile the current (uncommitted) procedural model:
            // the fast path when editing, and what makes a side-by-side result
            // view feel live. No commit — the user commits only when happy.
            const cb = useCellBuilderStore.getState();
            // Compile when there are uncommitted changes OR when the wanted
            // result isn't in the scene yet — so ⇧↵ on an unchanged model with
            // no simulation/detail loaded still produces one. Mirrors the
            // Compile button's disabled state. See needsPreviewCompile.
            if (cb.active && needsPreviewCompile(cb)) {
                event.preventDefault();
                void cb.compilePreviewSelected();
            }
        } else if (shift && key === "w" && !ctrl && !event.altKey) {
            // Shift+W: gallery mode on / off.
            event.preventDefault();
            useGalleryStore.getState().toggle();
        } else if (shift && key === "q") {
            const {isOptionsVisible, setIsOptionsVisible} = useOptionsStore.getState();
            setIsOptionsVisible(!isOptionsVisible);
        } else if (shift && key === "p" && !ctrl && !event.altKey) {
            // Shift+P: the admin panel, toggled -- the same floating window as Options' "Admin panel"
            // button, and offered to the same people: for anyone else the key does nothing.
            if (!useMeStore.getState().isAdmin) return;
            const {open, openPanel, closePanel} = useViewerPanelStore.getState();
            event.preventDefault();
            if (open === "admin") closePanel();
            else openPanel("admin");
        } else if (shift && key === "r" && !ctrl && !event.altKey) {
            // Shift+R: start / end a render-profiling session (the Performance option "Record render
            // metrics"), administrators only. A render is requested so the profiler sees the switch
            // at once, not on the next camera move.
            if (!useMeStore.getState().isAdmin) return;
            event.preventDefault();
            const m = useViewMetricsStore.getState();
            m.setCollectRenderMetrics(!m.collectRenderMetrics);
            requestRender();
        } else if (shift && key === "t" && !ctrl && !event.altKey) {
            // Shift+T: open the tree at the selected element and focus it for the arrow keys (or,
            // open but unfocused, just focus it there). Closing is Shift+T or Esc from INSIDE the
            // tree, handled by the trees themselves -- a focused tree consumes its keys.
            // Not an Alt or Ctrl chord: the browser and OS own too many of those (Ctrl+T, Alt+T).
            event.preventDefault();
            toggleTreeFocus();
        } else if (shift && key === "c") {
            // Copy the name of each selected object to the clipboard,
            // one per line. Same routine the ObjectInfoBox button uses
            // on mobile — see utils/clipboard/copySelectionNames.
            void copySelectionNames(selectedObjects).then((n) => {
                if (n === 0) console.warn("Shift+C: nothing copied");
            });
        } else if (shift && key.startsWith("arrow") && t?.closest?.('[role="tree"]')) {
            // Inside a tree Shift+Arrow is the tree's own: grow or shrink the selection by a row.
            // Traversing levels as well would replace the range the tree just extended.
            return;
        } else if (shift && key === "arrowup") {
            // Tree-level traversal from the current selection (Shift is the
            // "activate traversal" modifier). Up = parent level; Down = first
            // child; Left/Right = previous/next sibling. Never opens the tree
            // panel — see utils/tree_view/treeNavigation. preventDefault stops
            // the arrows from also scrolling the page.
            event.preventDefault();
            void selectParentLevel();
        } else if (shift && key === "arrowdown") {
            event.preventDefault();
            void selectChildLevel();
        } else if (shift && key === "arrowleft") {
            event.preventDefault();
            void selectSibling(-1);
        } else if (shift && key === "arrowright") {
            event.preventDefault();
            void selectSibling(1);
        }
    };

    window.addEventListener("keydown", handleKeyDown);
    // An undo must never reach into a scene that has since been replaced.
    const unbindHistoryReset = bindViewerHistoryReset();

    // An emptied scene returns the camera to its starting pose. Subscribed to
    // the model state rather than hooked into the unload handlers because there
    // are several ways to unload -- the Scene panel's x, a plugin's
    // `ctx.scene.unloadModel`, a scene-wide clear -- and this is the one place
    // that sees all of them. Only the transition INTO empty resets, so a user
    // who framed a detail and unloaded nothing keeps their view.
    let wasLoaded = useModelState.getState().loadedSourceNames.size > 0;
    const unsubscribeLoaded = useModelState.subscribe((state) => {
        const isLoaded = state.loadedSourceNames.size > 0;
        if (wasLoaded && !isLoaded) resetCameraHome(camera, controls);
        wasLoaded = isLoaded;
    });

    return () => {
        unsubscribeLoaded();
        unbindHistoryReset();
        window.removeEventListener("keydown", handleKeyDown);
        if (scopeEl) {
            if (onEnter) scopeEl.removeEventListener("mouseenter", onEnter);
            if (onLeave) scopeEl.removeEventListener("mouseleave", onLeave);
            if (onFocusIn) scopeEl.removeEventListener("focusin", onFocusIn);
            if (onFocusOut) scopeEl.removeEventListener("focusout", onFocusOut as EventListener);
        }
    };
}

/** Shift+F: frame the selection. Shared with the 3D mouse's buttons. */
export function fitSelectionView(camera: THREE.PerspectiveCamera, controls: CameraControls | OrbitControls): void {
    // Builder cells are __excludeFromFit (they're a tool overlay), so
    // centerViewOnSelection finds nothing for a cell selection — frame
    // the selected cells directly, same as the mobile "Go to object"
    // button. Falls through to the draw-range selection otherwise.
    const cb = useCellBuilderStore.getState();
    if (cb.active !== null && cb.selection !== null && frameCells(cb.selectedCellIds, controls, camera)) {
        requestRender();
    } else {
        centerViewOnSelection(controls, camera);
    }
}

/** Shift+A: frame everything. Shared with the 3D mouse's buttons. */
export function fitAllView(
    scene: THREE.Scene,
    camera: THREE.PerspectiveCamera,
    controls: CameraControls | OrbitControls,
): void {
    // Likewise "fit all": a procedural model in the cellbuilder shows
    // only excluded cell boxes until it's compiled, so zoomToAll would
    // frame nothing — frame all cells instead when the builder is active.
    const cb = useCellBuilderStore.getState();
    if (cb.active !== null && Object.keys(cb.cells).length > 0 && frameCells("all", controls, camera)) {
        requestRender();
    } else {
        zoomToAll(scene, camera, controls);
    }
}

/** The pose `setupCamera` starts every viewer at, and the clipping it starts
 *  with. Kept here beside the code that overwrites them. */
const HOME_POSITION = new THREE.Vector3(-5, 5, 5);
const HOME_TARGET = new THREE.Vector3(0, 0, 0);
const HOME_NEAR = 0.1;
const HOME_FAR = 10000;

/** Put the camera back where the viewer started.
 *
 * WHY THIS IS NEEDED AT ALL. `zoomToAll` returns early on a scene with no
 * meshes -- correctly, since there is nothing to frame -- so unloading the last
 * model leaves the camera wherever the user had dragged it, and "fit all" then
 * does nothing because there is still nothing to fit. The viewer is empty and
 * unrecoverable except by reloading the page.
 *
 * NEAR AND FAR ARE RESET TOO, and that is the part that is easy to miss.
 * `applyAdaptiveClipping` scales them to the model being framed, so after a
 * large model they are left wide -- and the next small model loads clipped into
 * invisibility, which reads as "the load failed" rather than "the clipping is
 * stale". Position without clipping would be a half reset. */
export const resetCameraHome = (
    camera: THREE.PerspectiveCamera,
    controls: OrbitControls | CameraControls,
) => {
    camera.near = HOME_NEAR;
    camera.far = HOME_FAR;
    camera.position.copy(HOME_POSITION);
    camera.updateProjectionMatrix();
    if (controls instanceof CameraControls) {
        // setLookAt with no transition already applies the pose; CameraControls'
        // own update() takes a frame delta and is driven by the render loop.
        void controls.setLookAt(
            HOME_POSITION.x, HOME_POSITION.y, HOME_POSITION.z,
            HOME_TARGET.x, HOME_TARGET.y, HOME_TARGET.z,
            false,
        );
    } else {
        controls.target.copy(HOME_TARGET);
        controls.update();
    }
    requestRender();
};

