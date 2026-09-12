// FEA streaming: which renderings of the loaded result are on screen.
//
// Owns: the visibility rule for a beam's three renderings (section solid,
// result-coloured line, grey element edge), the result-colour switch, the
// element-edge toggles, and the undeformed reference wireframe. Everything
// here flips flags on objects the session already holds; nothing is fetched
// or painted. Whether result colours are on screen right now is recorded on
// the session handle (`resultColorsShown`), not here: it is a fact about the
// loaded model and goes with it.
//
// Inputs: the session handle (`feaSession`) for the meshes, and
// `useFeaAnimationStore` for the user's toggles.

import * as THREE from "three";

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {requestRender} from "@/state/perfStore";
import {hasResultLineSegments, setResultLineSegmentsVisible} from "../resultLineSegments";
import {setResultPointMarkersVisible} from "../resultPointMarkers";
import {clearUndeformedGhost, installUndeformedGhost} from "../undeformedGhost";
import {feaSession as session} from "./session";

/** Flip beam-solid mesh visibility on the active session, if any.
 *  Cheap — just toggles ``mesh.visible``; no re-fetch, no re-paint.
 *  No-op when no session is active or the manifest didn't ship a
 *  beam-solid mesh. */
export function setBeamSolidsVisible(visible: boolean): void {
    if (session.active?.beamSolidMesh) {
        session.active.beamSolidMesh.visible = visible;
    }
    syncFeaOverlayVisibility();
}

/** Every element-edge wireframe in the active session, main mesh and beam solids. */
function elementEdgeOverlays(name?: string): THREE.LineSegments[] {
    const names = name ? [name] : ["fea-element-edges", "fea-beam-element-edges"];
    const out: THREE.LineSegments[] = [];
    for (const parent of [session.active?.mesh, session.active?.beamSolidMesh]) {
        if (!parent) continue;
        for (const each of names) {
            const child = parent.getObjectByName(each);
            if (child instanceof THREE.LineSegments) out.push(child);
        }
    }
    return out;
}

/**
 * Decide which of the three renderings of a beam is on screen, in one place.
 *
 * A line element can be drawn three ways and the viewer builds all three: the
 * extruded section solid, the result-coloured fat line, and the grey element edge
 * from the mesh's edge sidecar. Each was switched by its own toggle, which is how
 * a beam came to be drawn twice — with the sections off, the coloured line and the
 * grey edge sat on the same two nodes, one following the morph and one following
 * the CPU driver, and the model appeared to have twice as many members as it has.
 *
 * The rule, stated once:
 *
 *   * solids show when the user asks for them;
 *   * the coloured line stands in for the solid, so it shows when the solids do
 *     not and result colouring is on;
 *   * the grey beam edge yields to the coloured line and to nothing else. It is
 *     what makes a beam visible with colouring off, and it is still the member's
 *     mesh line through the middle of a section solid.
 *
 * Shell edges are untouched: nothing else draws a shell's element boundaries.
 */
export function syncFeaOverlayVisibility(): void {
    if (!session.active?.mesh) return;
    const store = useFeaAnimationStore.getState();
    const solids = session.active.beamSolidMesh?.visible ?? false;
    // Built, wanted, and not superseded by the solids. All three: only an ELEMENT
    // field installs coloured lines, so a nodal field has none to stand in for the
    // grey edge and the beam would simply stop being drawn.
    //
    // "Wanted" is what is on screen, not only the user's toggle: a mode that owns
    // the scene colouring switches the colours off without touching that toggle,
    // and a coloured beam left behind under it is a result painted where none
    // should be.
    const resultColorsShown = session.active.resultColorsShown ?? true;
    const colouredLines =
        hasResultLineSegments(session.active.mesh) && store.resultColorsVisible && resultColorsShown && !solids;

    setResultLineSegmentsVisible(session.active.mesh, colouredLines);
    for (const overlay of elementEdgeOverlays("fea-element-edges")) {
        overlay.visible = store.elementEdgesVisible;
    }
    for (const overlay of elementEdgeOverlays("fea-beam-element-edges")) {
        overlay.visible = store.elementEdgesVisible && !colouredLines;
    }
    requestRender();
}

/**
 * Show or hide the element-edge wireframe on the loaded result.
 *
 * A RUNTIME toggle, unlike the ``hideElementEdges`` perf flag: that one is read
 * when the mesh is built and decides whether the overlay is created at all, so
 * flipping it does nothing to a model already on screen. This flips `visible` on
 * what exists, which is what a toolbar button has to do.
 *
 * The beam-solid wireframe stays subordinate to the solids themselves — hiding
 * edges must not reveal a wireframe for solids that are switched off.
 */
export function setFeaElementEdgesVisible(_visible: boolean): void {
    // Through the shared rule rather than a blanket flip. A beam's grey edge is
    // suppressed while another rendering already draws that element, and this
    // toggle must not be what puts it back. Every caller writes the store first,
    // so the argument is redundant; it is kept for the viewer-core contract.
    syncFeaOverlayVisibility();
}

/**
 * Paint the model with the result field, or show it in its base material.
 *
 * Off is not "no result" — the step, the field and the legend's range are all
 * still what they were. It is the geometry question separated from the value
 * question: turning colour off is how you look at the MESH, at a section cut, at
 * where a beam actually sits, without a contour on top of it.
 *
 * Every surface that carries the field is covered, not just the shells: the
 * beam-solid mesh, and the coloured beam lines that stand in for it when solids
 * are off. Leaving either behind would say the colouring was still partly on,
 * which is worse than not offering the switch.
 *
 * Recorded on the session as what is on screen, distinct from the store toggle:
 * a mode that owns the scene colouring switches the colours off without
 * recording that as the user's preference.
 */
export function setFeaResultColorsVisible(visible: boolean): void {
    if (session.active) session.active.resultColorsShown = visible;
    const setVc = (mat: THREE.Material) => {
        if ("vertexColors" in mat && (mat as unknown as {vertexColors: boolean}).vertexColors !== visible) {
            (mat as unknown as {vertexColors: boolean}).vertexColors = visible;
            mat.needsUpdate = true;
        }
    };
    for (const target of [session.active?.mesh, session.active?.beamSolidMesh]) {
        if (!target) continue;
        // The beam-solid mesh only carries vertex colours when a field actually
        // painted it; forcing them on would tint it by whatever is in the buffer.
        if (visible && target === session.active?.beamSolidMesh && !target.geometry.getAttribute("color")) continue;
        const m = target.material;
        if (Array.isArray(m)) m.forEach(setVc);
        else if (m) setVc(m as THREE.Material);
    }
    if (session.active?.mesh) {
        // Result-point markers are result colouring too.
        setResultPointMarkersVisible(session.active.mesh, visible);
    }
    // Which of a beam's three renderings is on screen changes with this, so the
    // shared rule decides rather than this function reaching for one of them.
    syncFeaOverlayVisibility();
}

/** Are element edges currently drawn? False when the bake carried none. */
export function feaElementEdgesVisible(): boolean {
    const overlays = elementEdgeOverlays();
    return overlays.length > 0 && overlays.some((o) => o.visible);
}

/** Does the loaded result carry an element-edge wireframe to toggle? */
export function hasFeaElementEdges(): boolean {
    return elementEdgeOverlays().length > 0;
}

/**
 * Show or hide the undeformed reference wireframe on the active FEA session.
 *
 * Reads the flag from the store rather than taking it, so a caller that has just
 * set the preference and a caller re-applying it after a load are the same call.
 * A no-op when nothing is loaded, or when the bake carried no edge sidecar —
 * there is no honest reference to draw from a triangulation alone.
 */
export function refreshUndeformedGhost(): void {
    if (!session.active?.mesh) return;
    const show = useFeaAnimationStore.getState().showUndeformed;
    if (!show || !session.active.edgeIndices || session.active.edgeIndices.length === 0) {
        clearUndeformedGhost(session.active.mesh);
        if (session.active.beamSolidMesh) clearUndeformedGhost(session.active.beamSolidMesh);
        requestRender();
        return;
    }
    installUndeformedGhost(session.active.mesh, session.active.basePositions, session.active.edgeIndices);
    requestRender();
}

/** Set the preference and apply it in one call — what a toolbar toggle wants. */
export function setFeaUndeformedGhost(show: boolean): void {
    useFeaAnimationStore.getState().setShowUndeformed(show);
    refreshUndeformedGhost();
}
