import * as THREE from "three";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {centerViewOnSelection} from "@/utils/scene/centerViewOnSelection";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {useGalleryStore} from "@/state/galleryStore";
import {useMeStore} from "@/state/meStore";
import {useViewMetricsStore} from "@/state/viewMetricsStore";
import {CORE_PANEL_IDS, toggle as togglePanel} from "@/layout/panelActions";
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
import {bindCoreShortcuts, type CoreShortcutHandler} from "@/layout/shortcuts";
import {installShortcutDispatcher} from "@/layout/shortcutDispatch";
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

    // The viewer's keys are core's registered shortcuts (layout/coreShortcutTable): this gives
    // them their handlers for as long as the viewer is mounted, scoped to `inScope`, and makes
    // sure the registry's one listener is running (the layout may not be, in an embed). The
    // listener ignores keys typed into a field and keys a widget already handled.
    const live = () => inScope;
    const shortcut = (run: () => void | boolean, when: CoreShortcutHandler["when"] = live): CoreShortcutHandler => ({run, when});
    const copyNames = () => {
        const selectedObjects = useSelectedObjectStore.getState().selectedObjects;
        void copySelectionNames(selectedObjects).then((n) => {
            if (n === 0) console.warn("Shift+C: nothing copied");
        });
    };
    // Inside a tree Shift+Arrow is the tree's own: grow or shrink the selection by a row.
    // Traversing levels as well would replace the range the tree just extended.
    const outsideTree: CoreShortcutHandler["when"] = (e) =>
        inScope && !(e?.target as HTMLElement | null | undefined)?.closest?.('[role="tree"]');
    // The procedural cell builder has its own history and takes Ctrl+Z / Ctrl+Y first.
    const history = (step: () => void) => () => {
        if (useCellBuilderStore.getState().active !== null) return false;
        step();
    };
    const unbindShortcuts = bindCoreShortcuts({
        "core:undo": shortcut(history(undoViewerAction)),
        "core:redo": shortcut(history(redoViewerAction)),
        "core:redo-alt": shortcut(history(redoViewerAction)),
        "core:hide-selected": shortcut(() => hideSelectedRanges()),
        "core:hide-others": shortcut(() => hideUnselectedRanges()),
        "core:group-selection": shortcut(() => void groupSelectionFromKeyboard()),
        "core:show-all": shortcut(() => unhideAllRanges()),
        "core:fit-selection": shortcut(() => fitSelectionView(camera, controls)),
        "core:fit-all": shortcut(() => fitAllView(scene, camera, controls)),
        // Shift+Enter: preview-compile the current (uncommitted) procedural model -- the fast
        // path when editing. Only when there is something to compile (mirrors the Compile
        // button's disabled state, see needsPreviewCompile); otherwise the key is not ours.
        "core:builder-compile": shortcut(() => {
            const cb = useCellBuilderStore.getState();
            if (!(cb.active && needsPreviewCompile(cb))) return false;
            void cb.compilePreviewSelected();
        }),
        "core:toggle-gallery": shortcut(() => useGalleryStore.getState().toggle()),
        "core:toggle-options": shortcut(() => void togglePanel(CORE_PANEL_IDS.options)),
        // The admin panel, toggled -- offered to the same people as Options' "Admin panel" button;
        // for anyone else the key does nothing.
        "core:toggle-admin": shortcut(() => {
            if (!useMeStore.getState().isAdmin) return false;
            togglePanel(CORE_PANEL_IDS.admin);
        }),
        // Start / end a render-profiling session (administrators only). A render is requested so
        // the profiler sees the switch at once, not on the next camera move.
        "core:render-profiling": shortcut(() => {
            if (!useMeStore.getState().isAdmin) return false;
            const m = useViewMetricsStore.getState();
            m.setCollectRenderMetrics(!m.collectRenderMetrics);
            requestRender();
        }),
        // Open the tree at the selected element and focus it for the arrow keys (or, open but
        // unfocused, just focus it there). Closing is Shift+T or Esc from INSIDE the tree,
        // handled by the trees themselves -- a focused tree consumes its keys.
        "core:tree-focus": shortcut(() => toggleTreeFocus()),
        "core:copy-names": shortcut(copyNames),
        // Ctrl/Cmd+C copies the selection's names -- the levels' own names when levels are
        // picked in the tree -- unless the user has highlighted text, which the browser copies.
        "core:copy-names-ctrl": shortcut(() => {
            if ((window.getSelection()?.toString() ?? "").length > 0) return false;
            if (useSelectedObjectStore.getState().selectedObjects.size === 0 && !useTreeViewStore.getState().tree?.selectedNodes.length) return false;
            copyNames();
        }),
        // Tree-level traversal from the current selection. Never opens the tree panel -- see
        // utils/tree_view/treeNavigation.
        "core:select-parent": shortcut(() => void selectParentLevel(), outsideTree),
        "core:select-child": shortcut(() => void selectChildLevel(), outsideTree),
        "core:select-prev-sibling": shortcut(() => void selectSibling(-1), outsideTree),
        "core:select-next-sibling": shortcut(() => void selectSibling(1), outsideTree),
    });
    const releaseDispatcher = installShortcutDispatcher();

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
        releaseDispatcher();
        unbindShortcuts();
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

