// FEA streaming: the element-edge wireframe overlays.
//
// Owns: fetching the bake's edge sidecar (and the beam-edge split, when the
// bake wrote one) and attaching the shell and beam LineSegments overlays to
// the result mesh. They share the mesh's position attribute; sharing its
// morph state is `linkLineMorphToMesh`'s job once a field has been painted.
// Best-effort: a missing or corrupt sidecar logs and leaves the mesh bare.
//
// Inputs: the result mesh, a blob fetcher, the manifest, and the
// `hideElementEdges` perf opt-out.

import * as THREE from "three";

import type {FeaFetcher} from "@/services/fea/feaFetcher";
import {fetchMeshEdges} from "@/services/feaMeshEdges";
import type {FeaManifest} from "@/services/viewerApi";
import {usePerfStore} from "@/state/perfStore";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {FEA_BEAM_EDGE_COLOR, FEA_EDGE_COLOR} from "../edgeColors";
import {withoutEdges} from "../edgeSplit";

// Zoomed out, a dense element grid draws a line every few pixels and washes the result colours
// out to the edge colour. Fade the shell grid by how far apart its edges are on screen: full
// strength from EDGE_FULL_PX apart, gone at EDGE_GONE_PX.
const EDGE_FULL_PX = 12;
const EDGE_GONE_PX = 3;

/** Median length of a sample of `edges` (index pairs into `position`), in model units. */
function typicalEdgeLength(position: THREE.BufferAttribute, edges: Uint32Array): number {
    const pairs = edges.length / 2;
    const step = Math.max(1, Math.floor(pairs / 2000));
    const lengths: number[] = [];
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    for (let i = 0; i < pairs; i += step) {
        a.fromBufferAttribute(position, edges[i * 2]);
        b.fromBufferAttribute(position, edges[i * 2 + 1]);
        lengths.push(a.distanceTo(b));
    }
    lengths.sort((x, y) => x - y);
    return lengths[Math.floor(lengths.length / 2)] ?? 0;
}

/** Fade `segments` (an element grid on `mesh`) by its on-screen density, every frame it draws. */
export function fadeWithDensity(segments: THREE.LineSegments, mesh: THREE.Mesh, edges: Uint32Array): void {
    const length = typicalEdgeLength(mesh.geometry.attributes.position as THREE.BufferAttribute, edges);
    if (!(length > 0)) return;
    const material = segments.material as THREE.LineBasicMaterial;
    const size = new THREE.Vector2();
    const centre = new THREE.Vector3();
    segments.onBeforeRender = (renderer, _scene, camera) => {
        const geometry = mesh.geometry;
        if (!geometry.boundingSphere) geometry.computeBoundingSphere();
        centre.copy(geometry.boundingSphere!.center).applyMatrix4(mesh.matrixWorld);
        // CSS pixels per model unit at the model's centre.
        const cssHeight = renderer.getDrawingBufferSize(size).y / renderer.getPixelRatio();
        const ortho = camera as THREE.OrthographicCamera;
        const perUnit = ortho.isOrthographicCamera
            ? (cssHeight * ortho.zoom) / (ortho.top - ortho.bottom)
            : cssHeight / (2 * Math.max(centre.distanceTo(camera.position), 1e-9) *
                Math.tan(THREE.MathUtils.degToRad((camera as THREE.PerspectiveCamera).fov) / 2));
        const px = length * perUnit;
        material.opacity = THREE.MathUtils.clamp((px - EDGE_GONE_PX) / (EDGE_FULL_PX - EDGE_GONE_PX), 0, 1);
    };
}

/** Attach the element-edge overlays to `mesh` and return the full edge index
 *  the bake shipped (the caller keeps it for the undeformed reference
 *  wireframe), or null when none was installed. */
export async function installElementEdges(
    mesh: THREE.Mesh,
    fetcher: FeaFetcher,
    manifest: FeaManifest,
): Promise<Uint32Array | null> {
    let installed: Uint32Array | null = null;
    // Element-edge wireframe overlay. The bake emits an explicit
    // edge sidecar (deduped uint32 pairs from each cell's
    // ElemShape.edges) so the wireframe shows real element
    // boundaries — not the diagonals from quad-face triangulation.
    // Sharing the mesh's position attribute + morph attribute +
    // influences array means deformation drives both face and
    // line rendering from a single buffer / single uniform.
    if (manifest.mesh.edges_url && !usePerfStore.getState().hideElementEdges) {
        try {
            const edgeIndices = await fetchMeshEdges(
                fetcher,
                manifest.mesh.edges_url,
            );
            installed = edgeIndices;

            // Beams get their own, dimmer colour. A shell's element edges are a
            // grid you read element size off; a beam's edge is a member. In one
            // colour the members vanish into the grid, so the bake now says
            // which edges are which and they are drawn as two overlays.
            //
            // Split rather than overdrawn: the same pair painted twice at the
            // same depth is a z-fight, and which colour wins is then decided by
            // the driver.
            let lineEdgeIndices: Uint32Array | null = null;
            if (manifest.mesh.line_edges_url) {
                try {
                    const fetched = await fetchMeshEdges(
                        fetcher,
                        manifest.mesh.line_edges_url,
                    );
                    if (fetched.length > 0) lineEdgeIndices = fetched;
                } catch (err) {
                    // A missing or unreadable split is not worth failing a load
                    // over — everything simply stays one colour, as before.
                    // eslint-disable-next-line no-console
                    console.warn("[fea-streaming] failed to load line edges:", err);
                }
            }
            const shellEdgeIndices = lineEdgeIndices
                ? withoutEdges(edgeIndices, lineEdgeIndices)
                : edgeIndices;

            if (shellEdgeIndices.length > 0) {
                const lineGeom = new THREE.BufferGeometry();
                lineGeom.setAttribute("position", mesh.geometry.attributes.position);
                lineGeom.setIndex(new THREE.BufferAttribute(shellEdgeIndices, 1));
                const lineMat = new THREE.LineBasicMaterial({
                    color: FEA_EDGE_COLOR,
                    depthTest: true,
                    // Transparent (opacity 1 until zoomed out: fadeWithDensity) so the
                    // element-edge wireframe joins the transparent render pass
                    // and, with the renderOrder below, sorts ABOVE a plugin
                    // field/utilisation face overlay instead of being painted
                    // over + z-fighting it (which read as flicker on the
                    // element edges). Opaque lines would render in the opaque
                    // pass, before any transparent overlay draws over them.
                    transparent: true,
                });
                const segments = new THREE.LineSegments(lineGeom, lineMat);
                segments.name = "fea-element-edges";
                // Above field/plugin face overlays (renderOrder 2), below the
                // selection highlight (renderOrder 8), so element edges stay
                // legible through a field overlay without hiding selection.
                segments.renderOrder = 3;
                // Clip the element-edge wireframe with the model under section planes,
                // including planes enabled before this (awaited) edge fetch finished.
                clipWithModel(segments);
                // Layer 1: rendered (camera enables layers 0+1) but
                // not pickable (setupPointerHandler's raycaster
                // explicitly disables layer 1). prepareLoadedModel
                // does the same to the GLB's own LineSegments, but
                // it runs before this block — our streaming wireframe
                // is added afterwards, so we have to set the layer
                // ourselves. Without it, shell elements (where line
                // and triangle are coplanar) let the line win the
                // raycaster's distance race; the click resolves to
                // a LineSegments with no unique_key and selection
                // fails with "No drawRanges found for key: undefined".
                segments.layers.set(1);
                fadeWithDensity(segments, mesh, shellEdgeIndices);
                // Share the mesh's morph attribute + influences
                // array so the line wireframe morphs in lockstep
                // with the face mesh. We set this *after* the
                // first applyFieldToMesh call below seeds the
                // morph attribute — see linkLineMorphToMesh.
                mesh.add(segments);
            }

            // The beam edges, same geometry and morph story, own colour.
            if (lineEdgeIndices && lineEdgeIndices.length > 0) {
                const beamGeom = new THREE.BufferGeometry();
                beamGeom.setAttribute("position", mesh.geometry.attributes.position);
                beamGeom.setIndex(new THREE.BufferAttribute(lineEdgeIndices, 1));
                const beamMat = new THREE.LineBasicMaterial({
                    color: FEA_BEAM_EDGE_COLOR,
                    depthTest: true,
                    transparent: true,
                });
                const beamSegments = new THREE.LineSegments(beamGeom, beamMat);
                beamSegments.name = "fea-beam-element-edges";
                beamSegments.renderOrder = 3;
                clipWithModel(beamSegments);
                beamSegments.layers.set(1);
                mesh.add(beamSegments);
            }
        } catch (err) {
            // Wireframe overlay is decorative — log and continue
            // so a missing/corrupt sidecar doesn't block rendering.
            // eslint-disable-next-line no-console
            console.warn("[fea-streaming] failed to load mesh edges:", err);
        }
    }
    return installed;
}
