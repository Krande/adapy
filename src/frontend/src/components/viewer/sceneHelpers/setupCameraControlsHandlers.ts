import * as THREE from "three";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {centerViewOnSelection} from "@/utils/scene/centerViewOnSelection";
import {OrbitControls} from "three/examples/jsm/controls/OrbitControls";
import {useOptionsStore} from "@/state/optionsStore";
import {useTreeViewStore} from "@/state/treeViewStore";
import CameraControls from "camera-controls";
import {copySelectionNames} from "@/utils/clipboard/copySelectionNames";
import {hideSelectedRanges, unhideAllRanges} from "@/utils/scene/visibility";
import {applyAdaptiveClipping} from "@/components/viewer/sceneHelpers/adaptiveClipping";

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

        if (shift && key === "h") {
            hideSelectedRanges();
        } else if (shift && key === "u") {
            unhideAllRanges();
        } else if (shift && key === "f") {
            centerViewOnSelection(controls, camera);
        } else if (shift && key === "a") {
            zoomToAll(scene, camera, controls);
        } else if (shift && key === "q") {
            const {isOptionsVisible, setIsOptionsVisible} = useOptionsStore.getState();
            setIsOptionsVisible(!isOptionsVisible);
        } else if (shift && key === "t") {
            const {isTreeCollapsed, setIsTreeCollapsed} = useTreeViewStore.getState();
            setIsTreeCollapsed(!isTreeCollapsed);
        } else if (shift && key === "c") {
            // Copy the name of each selected object to the clipboard,
            // one per line. Same routine the ObjectInfoBox button uses
            // on mobile — see utils/clipboard/copySelectionNames.
            void copySelectionNames(selectedObjects).then((n) => {
                if (n === 0) console.warn("Shift+C: nothing copied");
            });
        }
    };

    window.addEventListener("keydown", handleKeyDown);

    return () => {
        window.removeEventListener("keydown", handleKeyDown);
        if (scopeEl) {
            if (onEnter) scopeEl.removeEventListener("mouseenter", onEnter);
            if (onLeave) scopeEl.removeEventListener("mouseleave", onLeave);
            if (onFocusIn) scopeEl.removeEventListener("focusin", onFocusIn);
            if (onFocusOut) scopeEl.removeEventListener("focusout", onFocusOut as EventListener);
        }
    };
}

// Skip helper subtrees (section planes, caps, the transform gizmo) and hidden
// objects when fitting the camera. setFromObject ignores `visible`, so the
// gizmo — which scales with camera distance — would otherwise blow up the box
// and fling the camera off into empty space (the "solid grey" screen).
const isExcludedFromFit = (obj: THREE.Object3D): boolean => {
    let cur: THREE.Object3D | null = obj;
    while (cur) {
        if (!cur.visible || cur.userData?.__excludeFromFit) return true;
        cur = cur.parent;
    }
    return false;
};

export const zoomToAll = (scene: THREE.Scene, camera: THREE.PerspectiveCamera, controls: OrbitControls | CameraControls) => {
    // Compute bounding box only from imported/visible meshes, excluding helpers
    // like GridHelper, the section caps/stencil and the transform gizmo.
    const overallBox = new THREE.Box3();
    let hasMesh = false;

    scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh && !isExcludedFromFit(obj)) {
            const objBox = new THREE.Box3().setFromObject(obj);
            if (!objBox.isEmpty() && isFinite(objBox.min.x) && isFinite(objBox.max.x)) {
                overallBox.union(objBox);
                hasMesh = true;
            }
        }
    });

    if (!hasMesh || overallBox.isEmpty()) return;

    // Compute a bounding sphere from the overall box for robust FOV-based fitting
    const sphere = overallBox.getBoundingSphere(new THREE.Sphere());
    if (!sphere || sphere.radius === 0 || !isFinite(sphere.radius)) return;

    const center = sphere.center.clone();
    const radius = sphere.radius;

    // Adapt near/far to model size so small models can be zoomed into without near-plane clipping.
    applyAdaptiveClipping(camera, controls, radius);

    // Compute required distance so the sphere fits both vertically and horizontally
    const vFov = THREE.MathUtils.degToRad(camera.fov);
    const aspect = camera.aspect || 1;
    const vDist = radius / Math.tan(vFov / 2);
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * aspect);
    const hDist = radius / Math.tan(hFov / 2);
    const distance = Math.max(vDist, hDist);

    // Move the camera back along its current viewing direction
    const direction = new THREE.Vector3();
    camera.getWorldDirection(direction).normalize();
    const newPosition = center.clone().add(direction.clone().multiplyScalar(-distance));

    if (controls instanceof OrbitControls) {
        camera.position.copy(newPosition);
        camera.lookAt(center);
        controls.target.copy(center);
    } else if (controls instanceof CameraControls) {
        controls.setLookAt(
            newPosition.x, newPosition.y, newPosition.z,
            center.x, center.y, center.z,
            true // enable smooth transition
        );
    }

    camera.updateProjectionMatrix();
    if (controls instanceof OrbitControls) {
        controls.update();
    }
};
