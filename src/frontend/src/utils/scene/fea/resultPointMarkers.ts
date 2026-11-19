import * as THREE from "three";

import type {FeaManifestField} from "@/services/viewerApi";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {clipWithModel} from "../section_clipping";

const RESULT_POINT_MARKERS = "__fea_result_point_markers__";

/** Does painting this field draw result-point markers? */
export function fieldHasResultPointMarkers(field: Pick<FeaManifestField, "support"> | null | undefined): boolean {
    return field?.support === "result_point" || field?.support === "line_result_point";
}

/** Whether the markers are on screen: result colouring is shown and the user has
 *  not switched the markers off. */
export function resultPointMarkersShown(resultColorsShown: boolean, markersWanted: boolean): boolean {
    return resultColorsShown && markersWanted;
}

/** Show or hide the installed markers without discarding them. */
export function setResultPointMarkersVisible(mesh: THREE.Mesh, visible: boolean): void {
    mesh.userData.__feaMarkersShown = visible;
    const existing = mesh.getObjectByName(RESULT_POINT_MARKERS);
    if (existing) existing.visible = visible;
}

/** The installed markers, or null when the last paint drew none. */
export function resultPointMarkers(mesh: THREE.Mesh): THREE.Points | null {
    const existing = mesh.getObjectByName(RESULT_POINT_MARKERS);
    return existing instanceof THREE.Points ? existing : null;
}

export function clearResultPointMarkers(mesh: THREE.Mesh): void {
    const existing = mesh.getObjectByName(RESULT_POINT_MARKERS) as THREE.Points | undefined;
    if (!existing) return;
    mesh.remove(existing);
    existing.geometry.dispose();
    const material = existing.material;
    if (Array.isArray(material)) material.forEach((item) => item.dispose());
    else material.dispose();
}

interface MarkerData {
    positions: Float32Array;
    colors: Float32Array;
    displacement: Float32Array;
    labels?: readonly number[];
}

/** The markers of the elements not hidden on `mesh` (by their range id, `E<label>`); all of them
 *  when nothing is hidden or the points name no element. */
export function shownMarkers(data: MarkerData, hidden: ReadonlySet<string> | null | undefined): MarkerData {
    const labels = data.labels;
    if (!hidden?.size || !labels?.length) return data;
    const keep: number[] = [];
    for (let p = 0; p < labels.length; p++) if (!hidden.has(`E${labels[p]}`)) keep.push(p);
    if (keep.length === labels.length) return data;
    const pick = (a: Float32Array) => {
        const out = new Float32Array(keep.length * 3);
        keep.forEach((p, i) => out.set(a.subarray(p * 3, p * 3 + 3), i * 3));
        return out;
    };
    return {positions: pick(data.positions), colors: pick(data.colors), displacement: pick(data.displacement), labels: keep.map((p) => labels[p])};
}

const hiddenOf = (mesh: THREE.Mesh): ReadonlySet<string> | undefined =>
    (mesh as unknown as {getHiddenRanges?: () => ReadonlySet<string>}).getHiddenRanges?.();

/** Draw the markers again on every hide, unhide and isolate of the mesh: once per mesh. */
function followHidden(mesh: THREE.Mesh): void {
    if (mesh.userData.__feaMarkersFollowHidden) return;
    const on = (mesh as unknown as {onHiddenChange?: (l: () => void) => () => void}).onHiddenChange;
    if (!on) return;
    mesh.userData.__feaMarkersFollowHidden = true;
    on.call(mesh, () => {
        const full = mesh.userData.__feaMarkersFull as MarkerData | undefined;
        // Shown or not as they were (the user's toggle, the result colours): none may be drawn now.
        if (full) drawMarkers(mesh, shownMarkers(full, hiddenOf(mesh)), mesh.userData.__feaMarkersShown as boolean | undefined);
    });
}

/** Install pixel-sized result markers with the same morph influence as their
 * parent FEA mesh. Positions and displacement are local to that mesh. `labels`: the element of
 * each marker, so a hidden element's markers are left out (and come back when it is shown). */
export function installResultPointMarkers(
    mesh: THREE.Mesh,
    positions: Float32Array,
    colors: Float32Array,
    displacement: Float32Array,
    labels?: readonly number[],
): void {
    const full: MarkerData = {positions, colors, displacement, labels};
    mesh.userData.__feaMarkersFull = full;
    followHidden(mesh);
    drawMarkers(mesh, shownMarkers(full, hiddenOf(mesh)));
}

function drawMarkers(mesh: THREE.Mesh, {positions, colors, displacement}: MarkerData, wasVisible?: boolean): void {
    clearResultPointMarkers(mesh);
    if (positions.length === 0) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    geometry.morphAttributes.position = [new THREE.BufferAttribute(displacement, 3)];
    geometry.morphTargetsRelative = true;
    const material = new THREE.PointsMaterial({
        size: 7,
        sizeAttenuation: false,
        vertexColors: true,
        depthTest: true,
    });
    const points = new THREE.Points(geometry, material);
    points.name = RESULT_POINT_MARKERS;
    points.frustumCulled = false;
    // Rebuilt on every repaint: start as the user left them, before the loader's
    // visibility pass runs.
    points.visible = wasVisible ?? useFeaAnimationStore.getState().resultPointMarkersVisible;
    points.updateMorphTargets();
    points.onBeforeRender = () => {
        const influence = mesh.morphTargetInfluences?.[0] ?? 0;
        if (points.morphTargetInfluences) points.morphTargetInfluences[0] = influence;
    };
    // Rebuilt on every repaint, so seeded with the section planes here, each time.
    clipWithModel(points);
    mesh.add(points);
}
