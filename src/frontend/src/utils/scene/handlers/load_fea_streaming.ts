import * as THREE from "three";

import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {runtime} from "@/runtime/config";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";

import {cacheAndBuildTree} from "@/state/model_worker/cacheModelUtils";
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {fetchFieldStep, makeViewerApiFetcher} from "@/services/feaFieldBlob";
import type {FeaFetcher, FeaRangeFetcher} from "@/services/fea/feaFetcher";
import {fetchBeamSolidsWarp, ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import {fetchMeshEdges} from "@/services/feaMeshEdges";
import {fetchMeshElements, MeshElementEntry} from "@/services/feaMeshElements";
import {convert_to_custom_batch_mesh} from "@/utils/scene/convert_to_custom_batch_mesh";
import {FeaManifest, FeaManifestField, viewerApi} from "@/services/viewerApi";
import {runResultSidecarLoaders} from "@/plugins/sidecarLoaders";
import type {SidecarFetcher} from "@/plugins/registry";
import {modelKeyMapRef, sceneRef} from "@/state/refs";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useColorStore} from "@/state/colorLegendStore";
import {useConversionStore} from "@/state/conversionStore";
import {usePerfStore, requestRender} from "@/state/perfStore";
import {applyFieldToMesh} from "../fea/applyField";
import {applyElemFieldToMesh} from "../fea/applyElemField";
import {resetFeaAnimationPhase} from "../fea/feaAnimationDriver";
import {clearGoToNode} from "../fea/goToNode";
import {selectedResultRange} from "../fea/resultUnits";
import {translationOffsets, warpValue} from "../fea/warpComponents";
import {autoWarpScale} from "../fea/warpScale";
import {beamSolidNodalColors} from "../fea/beamSolidNodalColors";
import {clearUndeformedGhost, installUndeformedGhost} from "../fea/undeformedGhost";
import {setResultLineSegmentsVisible} from "../fea/resultLineSegments";
import {setResultPointMarkersVisible} from "../fea/resultPointMarkers";
import {FEA_BEAM_EDGE_COLOR, FEA_EDGE_COLOR} from "../fea/edgeColors";
import {withoutEdges} from "../fea/edgeSplit";
import {expandSourceTriples, sourceVertexIndices} from "../fea/elementLocalGeometry";
import {useTableNavStore} from "@/state/tableNavStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {replace_model} from "./update_scene_from_message";

// Cached state for the currently-rendered FEA streaming source.
// Lets the picker re-apply with a different (component, step) on
// slider drag without re-fetching the mesh GLB or the field blob —
// switching steps within a single field becomes a synchronous
// in-memory operation.
interface ActiveFeaStreaming {
    sourceName: string;
    manifest: FeaManifest;
    /** The THREE mesh whose geometry we deform. */
    mesh: THREE.Mesh;
    /** Snapshot of the mesh's original positions, used to compute
     * displacement-from-base on every step change. */
    basePositions: Float32Array;
    /** The bake's element-edge index, kept so the undeformed reference wireframe
     *  can be built and rebuilt without re-fetching the sidecar. */
    edgeIndices?: Uint32Array;
    /** Optional beam-solid mesh — present when the manifest carries
     *  ``beam_solids_url``. Hosts beam (line) elements tessellated as
     *  3D extruded sections. Shares the FEA root group with the main
     *  mesh; the AFEL element-field path paints both meshes since
     *  beam labels live in both ``drawRanges`` maps (with a zero-
     *  triangle range on the main mesh and a real range here).
     *  No warp on this mesh in v1 — vertices aren't nodal. */
    beamSolidMesh?: THREE.Mesh;
    /** Base positions for the beam-solid mesh, snapshot at load. The
     *  AFEL kernel resets the position attribute to this snapshot
     *  before re-painting, mirroring the main-mesh path. */
    beamSolidBasePositions?: Float32Array;
    /** AFBV warp mapping — per-vertex (node0_idx, node1_idx, t). Used
     *  to lerp nodal displacements onto the solid mesh's vertices so
     *  the solid beams stay connected to the rest of the structure
     *  under any morph-scale factor. */
    beamSolidWarp?: ParsedBeamSolidsWarp;
    /** Optional LineSegments overlay rendering the beam-solid element
     *  boundaries (AFEG over the solid mesh). Position + morph
     *  attributes are linked to the beam-solid mesh after the first
     *  apply seeds the morph attribute. */
}

let active: ActiveFeaStreaming | null = null;

/** Drop the cached state on next call (e.g. when the user replaces
 * the scene with a different file). The blob cache lives separately
 * in feaFieldBlob.ts. Also resets the deformation-animation store
 * so the SimulationControls UI doesn't keep showing FEA-mode
 * controls for a mesh that's no longer in the scene, and hides the
 * controls panel entirely when no GLTF clips are around to show in
 * the fallback path. */
/** Flip beam-solid mesh visibility on the active session, if any.
 *  Cheap — just toggles ``mesh.visible``; no re-fetch, no re-paint.
 *  No-op when no session is active or the manifest didn't ship a
 *  beam-solid mesh. */
export function setBeamSolidsVisible(visible: boolean): void {
    if (active?.beamSolidMesh) {
        active.beamSolidMesh.visible = visible;
    }
    // The coloured lines are the OTHER rendering of the same elements, so they
    // show exactly when the solids do not. Drawing both would double the beam and
    // leave a fat line straddling every section.
    if (active?.mesh) {
        setResultLineSegmentsVisible(active.mesh, !visible);
    }
}

/** Every element-edge wireframe in the active session, main mesh and beam solids. */
function elementEdgeOverlays(): THREE.LineSegments[] {
    const out: THREE.LineSegments[] = [];
    for (const parent of [active?.mesh, active?.beamSolidMesh]) {
        if (!parent) continue;
        for (const name of ["fea-element-edges", "fea-beam-element-edges"]) {
            const child = parent.getObjectByName(name);
            if (child instanceof THREE.LineSegments) out.push(child);
        }
    }
    return out;
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
export function setFeaElementEdgesVisible(visible: boolean): void {
    for (const overlay of elementEdgeOverlays()) {
        overlay.visible = visible;
    }
    requestRender();
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
 */
export function setFeaResultColorsVisible(visible: boolean): void {
    const setVc = (mat: THREE.Material) => {
        if ("vertexColors" in mat && (mat as unknown as {vertexColors: boolean}).vertexColors !== visible) {
            (mat as unknown as {vertexColors: boolean}).vertexColors = visible;
            mat.needsUpdate = true;
        }
    };
    for (const target of [active?.mesh, active?.beamSolidMesh]) {
        if (!target) continue;
        // The beam-solid mesh only carries vertex colours when a field actually
        // painted it; forcing them on would tint it by whatever is in the buffer.
        if (visible && target === active?.beamSolidMesh && !target.geometry.getAttribute("color")) continue;
        const m = target.material;
        if (Array.isArray(m)) m.forEach(setVc);
        else if (m) setVc(m as THREE.Material);
    }
    if (active?.mesh) {
        const solids = active.beamSolidMesh?.visible ?? false;
        setResultLineSegmentsVisible(active.mesh, visible && !solids);
        // Result-point markers are result colouring too.
        setResultPointMarkersVisible(active.mesh, visible);
    }
    requestRender();
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
 * Does the loaded FEA model carry beam section geometry at all?
 *
 * `setBeamSolidsVisible` is a no-op without it — the bake only emits
 * ``beam_solids_url`` for a reader with section + axis info per beam, and only
 * when it was asked to. A UI that offers "beams as solid" needs to tell the two
 * cases apart: a toggle that flips and changes nothing reads as broken, where a
 * greyed one with a reason reads as a property of the model.
 */
export function hasBeamSolids(): boolean {
    return active?.beamSolidMesh != null;
}

/** The active FEA mesh (a custom-batch THREE.Mesh carrying per-element
 *  ``drawRanges``), or null when no FEA model is loaded. Exposed so a plugin can
 *  drive element-level scene ops (isolate / highlight / attach overlays) off the
 *  same mesh core deforms — reached via the plugin SceneHandle, never imported. */
export function getActiveFeaMesh(): THREE.Mesh | null {
    return active?.mesh ?? null;
}

/** Draw-range ids (e.g. ``E123``) currently selected on the active FEA mesh,
 *  or ``[]`` when nothing is selected / no FEA model is loaded. This is the same
 *  per-element selection the CustomBatchedMesh highlights (it reads the shared
 *  ``useSelectedObjectStore`` entry keyed on the active mesh). Exposed so a
 *  plugin drawing its own overlay on top of the FEA mesh can mirror core's
 *  selection highlight — reached via the plugin SceneHandle, never imported.
 *  Generic: names no plugin and returns the raw selection identity only. */
export function getActiveFeaSelectedRangeIds(): string[] {
    const mesh = active?.mesh;
    if (!mesh) return [];
    const selected = useSelectedObjectStore.getState().selectedObjects.get(mesh);
    return selected ? Array.from(selected) : [];
}

/** Drive core's per-element selection on the active FEA mesh from a set of
 *  draw-range ids. This writes the SAME ``useSelectedObjectStore`` entry that a
 *  scene click writes, so the highlight uses the exact selection colour +
 *  CustomBatchedMesh path as click-select — a plugin listing results should call
 *  this instead of painting its own overlay. ``additive`` false (default)
 *  replaces the selection; true unions with the current one. No-op when no FEA
 *  model is loaded. Generic: names no plugin, takes raw range ids only. */
export function setActiveFeaSelectedRangeIds(rangeIds: string[], additive = false): void {
    const mesh = active?.mesh;
    if (!mesh) return;
    const store = useSelectedObjectStore.getState();
    if (!additive) store.clearSelectedObjects();
    for (const id of rangeIds) store.addSelectedObject(mesh, id);
    requestRender();
}

export function clearActiveFeaStreaming(): void {
    active = null;
    useFeaAnimationStore.getState().reset();
    useColorStore.getState().setShowLegend(false);
    resetFeaAnimationPhase();
    // Drop any "go to node" marker + active-row state. The marker
    // mesh would otherwise survive into the next loaded model and
    // point at a vertex that no longer exists.
    clearGoToNode();
    useTableNavStore.getState().setActiveNodeId(null);
    useTableNavStore.getState().setGoToTarget(null);
    // Hide the panel — without this, the toggle button stays
    // pressed-state on a panel that has nothing useful to show.
    // Re-applying an FEA session sets it back to true.
    const generalAnimStore = useAnimationStore.getState();
    if (!generalAnimStore.hasAnimation) {
        generalAnimStore.setIsControlsVisible(false);
    }
}

function findFirstMesh(root: THREE.Object3D): THREE.Mesh | null {
    let found: THREE.Mesh | null = null;
    root.traverse((obj) => {
        if (found) return;
        if ((obj as THREE.Mesh).isMesh) {
            found = obj as THREE.Mesh;
        }
    });
    return found;
}

/** Build the draw-ranges + id-hierarchy userData entries that
 * prepareLoadedModel reads to wire CustomBatchedMesh selection.
 *
 * AFEM stores triangles per element; the index buffer counts vertex
 * indices, so we multiply ``tri_start`` and ``tri_count`` by 3 here.
 * Line elements (``triCount === 0``) are dropped from the draw-range
 * map but kept in id_hierarchy so name resolution still works for
 * them (selection won't fire on them via the triangle picker yet —
 * Phase 1.A doesn't wire line-element selection).
 *
 * The mesh is renamed to ``node0`` because the worker cache filter
 * (``cacheModelUtils.ts``) only consumes userData keys prefixed
 * ``draw_ranges_node`` — a quirk of how the CAD GLB pipeline names
 * primitives (``node0``, ``node0_1``, etc.). Without the rename,
 * the worker has no draw-range cache → ``queryMeshDrawRange``
 * returns null → click selection silently does nothing. The
 * id_hierarchy uses a synthetic root entry ``fea-root`` (parent
 * ``"*"``, the worker's root sentinel) so every element has a
 * resolvable parent.
 */
function installAfemUserData(
    gltf_scene: THREE.Group,
    entries: MeshElementEntry[],
): void {
    const mesh = findFirstMesh(gltf_scene);
    if (!mesh) {
        // Could be a line-only mesh exported as a PointCloud — no
        // selection to wire.
        return;
    }

    // Rename to a name the worker filter accepts. The filter is
    // hard-coded for "draw_ranges_node*" prefixes; renaming here is
    // less invasive than relaxing the filter.
    mesh.name = "node0";
    const finalName = mesh.name;

    // Tag the mesh so prepareLoadedModel skips the design-side edge
    // overlay (CustomBatchedMesh.getEdgeOverlay). That overlay is
    // built from originalGeometry with a static applyMatrix4 and
    // doesn't share morph attribute / influences, so it'd stay at
    // the un-deformed position while the face mesh + our AFEM-derived
    // wireframe morph. The AFEM wireframe already shows element
    // boundaries; per-edge selection highlight via the design-edge
    // shader isn't wired up for the streaming mesh anyway.
    mesh.userData.feaStreaming = true;

    const drawRanges: Record<string, [number, number]> = {};
    const idHierarchy: Record<string, [string, string | number]> = {};

    // Synthetic root: every element points to this as its parent.
    // The worker's root sentinel is "*" — without a concrete root
    // node referenced by parent="*", the tree would have multiple
    // roots and only the last element processed would survive as
    // the visible tree.
    const ROOT_RANGE_ID = "fea-root";
    idHierarchy[ROOT_RANGE_ID] = ["FEA elements", "*"];

    for (const entry of entries) {
        const rangeId = `E${entry.label}`;
        idHierarchy[rangeId] = [rangeId, ROOT_RANGE_ID];
        if (entry.triCount > 0) {
            drawRanges[rangeId] = [entry.triStart * 3, entry.triCount * 3];
        }
    }

    gltf_scene.userData[`draw_ranges_${finalName}`] = drawRanges;
    gltf_scene.userData["id_hierarchy"] = idHierarchy;
}

/** Pick the displacement field from the manifest. Frontend reads
 *  ``category`` set by the bake to find it without re-string-matching
 *  solver-specific names. Returns the first match or null. */
function findDisplacementField(manifest: FeaManifest): FeaManifestField | null {
    for (const f of manifest.fields) {
        if (f.category === "displacement") return f;
    }
    return null;
}

/** Resolve which field (and which step-values) drives the morph
 *  delta for this apply. The colour field is always the user's pick;
 *  warp is decoupled so stress / strain visualisations can still show
 *  the deformed shape. Returns ``null`` when the geometry should stay
 *  static (reaction fields, warp toggle off + no displacement field
 *  available, or the user picked displacement but warpEnabled is off). */
async function resolveWarpSource(
    rangeFetcher: FeaRangeFetcher,
    fetcher: FeaFetcher,
    cacheKey: string,
    manifest: FeaManifest,
    colorField: FeaManifestField,
    stepIndex: number,
    warpEnabled: boolean,
): Promise<{field: FeaManifestField; stepValues: Float32Array} | null> {
    // Reaction force fields never drive a deformation — applying them
    // as a morph delta would visualise a force vector as a
    // displacement, which is semantically wrong. Lock off regardless
    // of the toggle.
    if (colorField.category === "reaction") return null;

    // For the displacement field itself, the warp toggle still
    // controls whether the user sees the deformed shape — a user
    // inspecting raw DX values may want them on the un-deformed mesh.
    if (colorField.category === "displacement") {
        if (!warpEnabled) return null;
        const stepValues = await fetchFieldStep(rangeFetcher, fetcher, colorField, stepIndex, cacheKey);
        return {field: colorField, stepValues};
    }

    // Stress / strain / other — warp by the manifest's displacement
    // field when the user has the toggle on.
    if (!warpEnabled) return null;
    const dispField = findDisplacementField(manifest);
    if (!dispField) return null;

    // Step alignment: prefer the same index. If the displacement
    // field has fewer steps (rare — sub-step output), clamp to last.
    let warpStep = stepIndex;
    if (warpStep >= dispField.n_steps) {
        warpStep = dispField.n_steps - 1;
        // eslint-disable-next-line no-console
        console.warn(
            `[fea-streaming] colour-field step ${stepIndex} exceeds displacement-field ` +
            `n_steps=${dispField.n_steps}; clamping warp source to step ${warpStep}`,
        );
    }
    const stepValues = await fetchFieldStep(rangeFetcher, fetcher, dispField, warpStep, cacheKey);
    return {field: dispField, stepValues};
}

/** Fetch + parse the beam-solid GLB and its AFEM sidecar, returning a
 *  THREE.Mesh ready to attach to the scene with per-beam drawRanges
 *  already installed. Returns ``null`` if the manifest carries no
 *  beam-solid URL or the fetch failed (logged + non-fatal). */
async function tryLoadBeamSolids(
    fetcher: FeaFetcher,
    sourceName: string,
    manifest: FeaManifest,
    initialVisible: boolean,
): Promise<{mesh: THREE.Mesh; basePositions: Float32Array} | null> {
    const beamGlbUrl = manifest.mesh.beam_solids_url;
    if (!beamGlbUrl) return null;
    // Perf-store opt-out: when the user wants to A/B against the
    // line-element fallback we skip the GLB fetch + AFEM/AFBV parsing
    // entirely. Toggled live via the Performance panel; takes effect
    // on the next FEA stream load.
    if (usePerfStore.getState().hideBeamSolids) {
        return null;
    }

    try {
        const [buf, afemEntries] = await Promise.all([
            fetcher(beamGlbUrl),
            manifest.mesh.beam_solids_elements_url
                ? fetchMeshElements(fetcher, manifest.mesh.beam_solids_elements_url)
                : Promise.resolve<MeshElementEntry[]>([]),
        ]);
        const blob = new Blob([buf], {type: "model/gltf-binary"});
        const url = URL.createObjectURL(blob);
        let gltfMesh: THREE.Mesh | null = null;
        try {
            const loader = new GLTFLoader();
            const gltf = await new Promise<{scene: THREE.Group}>((resolve, reject) => {
                loader.load(url, resolve as never, undefined, reject);
            });
            gltfMesh = findFirstMesh(gltf.scene);
        } finally {
            URL.revokeObjectURL(url);
        }
        if (!gltfMesh) return null;

        // Build the draw-range Map keyed by ``E${label}`` so the AFEL
        // apply kernel and the click-resolver both find ranges with
        // the same lookup as the main mesh.
        const drawRanges = new Map<string, [number, number]>();
        for (const entry of afemEntries) {
            if (entry.triCount > 0) {
                drawRanges.set(`E${entry.label}`, [
                    entry.triStart * 3,
                    entry.triCount * 3,
                ]);
            }
        }
        // Rename to ``node1`` so the worker-cache filter accepts the
        // companion userData key. The main mesh is ``node0`` —
        // distinct names keep the two meshes' draw-range tables
        // separate in the worker cache.
        gltfMesh.name = "node1";
        gltfMesh.userData.feaBeamSolids = true;
        gltfMesh.userData.feaStreaming = true;
        gltfMesh.visible = initialVisible;

        // Upgrade to a CustomBatchedMesh so clicks resolve through
        // the existing picker pipeline (handleClickMesh → drawRanges
        // → range_id). Without this, raycasts hit a plain Mesh that
        // has no ``unique_key`` and the selection silently no-ops.
        const uniqueKey = `fea-beam-solids::${sourceName}`;
        const custom = convert_to_custom_batch_mesh(
            gltfMesh,
            drawRanges,
            uniqueKey,
            /* is_design */ false,
            /* ada_ext_data */ null,
        );
        // Preserve the userData tags + visibility flags the plain
        // mesh carried; convert_to_custom_batch_mesh copies userData
        // but it's worth being explicit so future tags don't get
        // lost to a helper refactor.
        custom.userData.feaBeamSolids = true;
        custom.userData.feaStreaming = true;
        custom.visible = initialVisible;

        // Register with the off-thread worker cache so the picker's
        // ``queryMeshDrawRange(unique_key, "node1", faceIndex)`` finds
        // the range and ``queryNameFromRangeId(unique_key, rangeId)``
        // returns the element label. Synthetic id_hierarchy with a
        // single FEA-beam root keeps name resolution flat — every
        // beam shows up as ``E${label}`` in the info box.
        const hierarchy: Record<string, [string, string | number]> = {};
        const rangesPlain: Record<string, [number, number]> = {};
        const ROOT_KEY = "fea-beam-solids-root";
        hierarchy[ROOT_KEY] = ["Beam solids", "*"];
        for (const entry of afemEntries) {
            if (entry.triCount > 0) {
                const rid = `E${entry.label}`;
                hierarchy[rid] = [rid, ROOT_KEY];
                rangesPlain[rid] = [entry.triStart * 3, entry.triCount * 3];
            }
        }
        // The Outliner resolves a clicked row through ``modelKeyMapRef``:
        // ``model_key`` -> an object whose subtree holds the named mesh.
        // ``setupModelLoader`` registers the main FEA mesh when it loads the GLB;
        // nothing registered this one. So clicking a beam in the tree set the
        // Properties name and made NO 3d selection -- the status bar stayed on
        // "No selection", nothing highlighted, and every selection-driven
        // behaviour was silently skipped for beams.
        if (!modelKeyMapRef.current) modelKeyMapRef.current = new Map();
        modelKeyMapRef.current.set(uniqueKey, custom);

        // Best-effort cache install — if it fails, the mesh still
        // renders, the click just won't resolve.
        void cacheAndBuildTree(uniqueKey, {
            id_hierarchy: hierarchy,
            draw_ranges_node1: rangesPlain,
        });

        // Don't flip vertexColors on here — without a color attribute,
        // three.js renders vertexColors=true geometry as black. The
        // AFEL apply kernel turns vertexColors on at the same time it
        // writes the color attribute, so the first paint lands both
        // together. Until then the GLB's base PBR material colour
        // shows, which is the right "no data" state for solid beams.

        const basePositions = snapshotBasePositions(custom.geometry);
        return {mesh: custom, basePositions};
    } catch (err) {
        // Beam-solid rendering is decorative — log and continue so a
        // missing/corrupt GLB doesn't block rendering of the main mesh.
        // eslint-disable-next-line no-console
        console.warn("[fea-streaming] failed to load beam-solid mesh:", err);
        return null;
    }
}

/** Install the beam-solid mesh's morph delta from a nodal
 *  displacement field. Per vertex:
 *
 *    delta_v = lerp(disp[node0], disp[node1], t) × (only first 3 components)
 *
 *  Linked to the main mesh's ``morphTargetInfluences`` so the slider
 *  drives both meshes in lockstep. No-op when the active session
 *  has no beam-solid mesh or no AFBV mapping. */
function installBeamSolidWarp(
    main: THREE.Mesh,
    beamSolid: THREE.Mesh,
    basePositions: Float32Array,
    warp: ParsedBeamSolidsWarp,
    warpField: FeaManifestField | undefined,
    warpStepValues: Float32Array | undefined,
): void {
    const nVerts = warp.n_verts;
    const displacement = new Float32Array(nVerts * 3);

    if (warpField && warpStepValues) {
        const nc = warpField.components.length;
        // WHICH slots hold the translation. A Sesam displacement field is
        // ["ALL","X","Y","Z","RX","RY","RZ"] -- reading slots 0..2 warps every
        // vertex by (ALL, X, Y), and since `ALL` is a non-negative aggregate the
        // beams visibly fly off. See translationOffsets.
        const axes = translationOffsets(warpField);
        const n0 = warp.node0;
        const n1 = warp.node1;
        const ts = warp.t;
        for (let v = 0; v < nVerts; v++) {
            const t = ts[v];
            const a = n0[v] * nc;
            const b = n1[v] * nc;
            const out = v * 3;
            const ax = warpValue(warpStepValues, a, axes[0]);
            const ay = warpValue(warpStepValues, a, axes[1]);
            const az = warpValue(warpStepValues, a, axes[2]);
            const bx = warpValue(warpStepValues, b, axes[0]);
            const by = warpValue(warpStepValues, b, axes[1]);
            const bz = warpValue(warpStepValues, b, axes[2]);
            const omt = 1 - t;
            displacement[out + 0] = omt * ax + t * bx;
            displacement[out + 1] = omt * ay + t * by;
            displacement[out + 2] = omt * az + t * bz;
        }
    }
    // Else: leave displacement at zero — no warp source means no
    // deformation, which is what the user gets when they pick a
    // reaction field or turn warp off.

    const geom = beamSolid.geometry;

    // In the geometry's CURRENT vertex numbering, which is not always the one the
    // warp sidecar is written in. Painting an element field expands this geometry to
    // element-local vertices and swaps its position buffer -- 33,812 vertices become
    // 172,260 -- and the expansion is cached, so it survives a switch back to a nodal
    // field. A morph sized for the original count is silently ignored by three.js,
    // which is why beam solids sat undeformed while everything around them moved.
    // `sourceVertexIndices` returns the cached map, or the identity when the geometry
    // was never expanded, so this is a no-op on the untouched case.
    const renderToSource = sourceVertexIndices(geom, nVerts);
    const renderPositions = expandSourceTriples(basePositions, renderToSource);
    const renderDisplacement = expandSourceTriples(displacement, renderToSource);

    const posAttr = geom.getAttribute("position");
    if (posAttr && posAttr.count === renderToSource.length) {
        (posAttr.array as Float32Array).set(renderPositions);
        posAttr.needsUpdate = true;
    }
    geom.morphAttributes.position = [new THREE.BufferAttribute(renderDisplacement, 3)];
    geom.morphTargetsRelative = true;

    // Share the main mesh's influences array so a single write to
    // mesh.morphTargetInfluences[0] (manual drag or RAF sweep)
    // moves both meshes. Same trick the line wireframe overlay uses.
    if (main.morphTargetInfluences) {
        beamSolid.morphTargetInfluences = main.morphTargetInfluences;
        beamSolid.morphTargetDictionary = main.morphTargetDictionary ?? undefined;
    } else if (!beamSolid.morphTargetInfluences) {
        beamSolid.morphTargetInfluences = [0];
        beamSolid.morphTargetDictionary = {displacement: 0};
    }

    // The beam-solid element-edge wireframe: same story as the main mesh's, and
    // the one the user sees as black lines hanging in space. Its index is written
    // against the ORIGINAL beam-solid vertices and it holds the position attribute
    // this geometry had before the element-local expansion swapped one in, so the
    // unexpanded `displacement` is what fits it. Installed BEFORE
    // linkLineMorphToMesh runs, which then leaves it alone precisely because it
    // brought its own.

    // Enable morph targets on every material slot so the GPU
    // actually applies the delta. The PBR material from the GLB
    // defaults to morphTargets=false.
    const enableMorph = (mat: THREE.Material) => {
        if ("morphTargets" in mat && (mat as unknown as {morphTargets: unknown}).morphTargets !== true) {
            (mat as unknown as {morphTargets: boolean}).morphTargets = true;
            mat.needsUpdate = true;
        }
    };
    if (Array.isArray(beamSolid.material)) beamSolid.material.forEach(enableMorph);
    else if (beamSolid.material) enableMorph(beamSolid.material as THREE.Material);

    // Same dispose dance as applyField: drop the cached morph texture
    // so three.js rebuilds it from the fresh BufferAttribute on the
    // next render.
    geom.dispatchEvent({type: "dispose"});
}

function snapshotBasePositions(geometry: THREE.BufferGeometry): Float32Array {
    const attr = geometry.getAttribute("position");
    if (!attr || attr.itemSize !== 3) {
        throw new Error("FEA mesh GLB has no usable position attribute");
    }
    return new Float32Array(attr.array as Float32Array);
}

/** Load the mesh GLB, fetch the chosen field's blob, and apply the
 * (component, step) selection. Subsequent calls for the same source
 * + field skip the network and just swap the step. */
export async function load_fea_streaming(args: {
    sourceName: string;
    manifest: FeaManifest;
    /** null = field-less mesh (design-model FEM): load mesh + beam-solids only, no result
     *  coloring / warp / step animation. */
    fieldName: string | null;
    stepIndex: number;
    reduction: string | null;
    displacementScale?: number;
    /** Colormap ID — one of the keys in ``COLORMAPS``. Optional so
     * existing call-sites that don't care still work; we fall back to
     * the active store value (and from there to viridis if unset). */
    colormap?: string;
    /** Optional stage reporter so the toast can show mesh-load /
     *  render progress, not just the manifest poll. ``progress`` is
     *  a fraction in [0, 1] over the load_fea_streaming portion of
     *  the flow; the caller is responsible for remapping that into
     *  the wider queue+convert+load progress bar. */
    onStage?: (stage: string, progress: number) => void;
    /** Optional abort signal — checked between async stages so the
     *  user clicking Kill in the toast bails out without waiting for
     *  the in-flight fetch (which doesn't itself accept a signal). */
    signal?: AbortSignal;
}): Promise<void> {
    if (!runtime.isRestMode()) {
        throw new Error("FEA streaming viewer is only available in REST mode");
    }
    const {sourceName, manifest, fieldName, stepIndex, reduction, onStage, signal} = args;
    const displacementScale = args.displacementScale ?? 1;
    const colormap =
        args.colormap ?? useFeaAnimationStore.getState().colormap;
    const stage = (label: string, progress: number) => {
        if (onStage) onStage(label, progress);
    };
    const throwIfAborted = () => {
        if (signal?.aborted) {
            throw new DOMException("load_fea_streaming aborted", "AbortError");
        }
    };

    if (!manifest || !Array.isArray(manifest.fields)) {
        throw new Error(
            "load_fea_streaming: manifest is missing or has no fields array",
        );
    }
    // fieldName == null is the field-less mesh path (design-model FEM): no field to resolve.
    const field =
        fieldName == null ? null : manifest.fields.find((f) => f.name_canonical === fieldName) ?? null;
    if (fieldName != null) {
        if (!field) {
            throw new Error(`field ${fieldName} not found in manifest`);
        }
        if (stepIndex < 0 || stepIndex >= field.n_steps) {
            throw new Error(
                `step index ${stepIndex} out of range (0..${field.n_steps - 1})`,
            );
        }
    }

    const scope = scopeUrlPart(useScopeStore.getState().current);
    // One fetcher + cache key for every storage-layer call below. The
    // bake-job storage convention (`_derived/<src>.fea/<filename>`)
    // is encoded in `makeViewerApiFetcher`; downstream helpers stay
    // storage-agnostic so paradoc-embed can plug in its own fetcher
    // that hits paradoc-serve's REST endpoint instead.
    const {fetcher, rangeFetcher, cacheKey} = makeViewerApiFetcher(scope, sourceName);

    // (Re-)load the mesh into the scene if we don't already have it
    // for this source. Switching field-within-source keeps the same
    // mesh; switching source forces a reload.
    if (!active || active.sourceName !== sourceName) {
        stage("loading mesh", 0.05);
        throwIfAborted();
        const buf = await fetcher(manifest.mesh.url);
        throwIfAborted();
        stage("loading mesh", 0.35);
        const blob = new Blob([buf], {type: "model/gltf-binary"});
        const url = URL.createObjectURL(blob);

        // Fetch the AFEM sidecar (per-element draw ranges) up-front.
        // The prepareHook installs userData entries before
        // prepareLoadedModel runs, so the FEA mesh enters the scene
        // as a per-element CustomBatchedMesh — same pick + highlight
        // pipeline as CAD models, no parallel selection path.
        let afemEntries: MeshElementEntry[] = [];
        if (manifest.mesh.elements_url) {
            try {
                afemEntries = await fetchMeshElements(
                    fetcher,
                    manifest.mesh.elements_url,
                );
            } catch (err) {
                // Selection wiring is best-effort: the picker still
                // renders without it, just at whole-mesh granularity.
                // eslint-disable-next-line no-console
                console.warn("[fea-streaming] failed to load mesh elements:", err);
            }
        }

        // Captured from the prepareHook so the mesh lookups below are
        // scoped to the GLB we just loaded — NOT the whole scene. The
        // fem_concepts overlay (and any other helper) registers its own
        // meshes as direct scene children, so findFirstMesh(scene) could
        // otherwise grab a glyph mesh as the "FEA mesh" and the field
        // apply would crash on a vertex-count mismatch. gltf_scene is the
        // same object setupModelLoader adds to the scene, so it stays
        // valid after replace_model resolves.
        let feaRoot: THREE.Object3D | null = null;
        try {
            const feaGroup = await replace_model(url, async (gltf_scene) => {
                feaRoot = gltf_scene;
                if (afemEntries.length > 0) {
                    installAfemUserData(gltf_scene, afemEntries);
                }
            }, undefined, /* translate */ true);
            const ms = useModelState.getState();
            ms.setModelUrl(url, SceneOperations.REPLACE);
            ms.setLoadedSourceName(sourceName);
            // Register the loaded group AFTER setLoadedSourceName (which clears
            // loadedSourceGroups) so the FEA result mesh gets a working visibility
            // toggle in the loaded-models list (hide it to inspect a sibling CAD
            // overlay). fem_concepts glyphs live as separate scene children, so
            // this only gates the result mesh — exactly what we want.
            if (feaGroup && sourceName) {
                ms.registerLoadedSource(sourceName, feaGroup);
            }
            // Register CAD↔FEA lineage from the manifest. Mirrors the
            // glTF-extension registration that setupModelLoader does
            // for CAD GLBs — once a sibling CAD overlay carrying the
            // same ``assembly_guid`` is also loaded, the panel's link
            // row resolves a clicked FEA element back to its parent
            // beam without going through the server.
            if (manifest.lineage && manifest.lineage.assembly_guid) {
                const sceneRoot = sceneRef.current;
                const meshRoot = feaRoot ? findFirstMesh(feaRoot) : null;
                const root = (meshRoot ?? feaRoot ?? sceneRoot) as THREE.Object3D | null;
                if (root) {
                    const materials = manifest.lineage.materials ?? {};
                    const sections = manifest.lineage.sections ?? {};
                    const {useLineageStore} = await import("@/state/lineageStore");
                    useLineageStore.getState().register({
                        kind: "fea",
                        fileName: sourceName,
                        assemblyGuid: manifest.lineage.assembly_guid,
                        root,
                        groups: manifest.lineage.groups.map((g) => {
                            // Resolve material + section name refs into
                            // a synthetic Beam/Plate metadata dict the
                            // Properties panel can render the same way
                            // it renders embedded CAD metadata. Cheap —
                            // one lookup per group (not per element).
                            const material =
                                (g.material_name && materials[g.material_name]) ||
                                (g.material_name ? {name: g.material_name} : null);
                            let metadata: any = null;
                            if (g.type === 'Beam') {
                                const section =
                                    (g.section_name && sections[g.section_name]) || null;
                                metadata = {
                                    type: 'Beam',
                                    name: g.parent_object_name ?? undefined,
                                    section,
                                    material,
                                };
                            } else if (g.type === 'Plate') {
                                metadata = {
                                    type: 'Plate',
                                    name: g.parent_object_name ?? undefined,
                                    thickness: g.thickness ?? null,
                                    material,
                                };
                            }
                            return {
                                parentObjectGuid: g.parent_object_guid,
                                inlineMembers: g.members,
                                metadata,
                            };
                        }),
                    });
                }
            }

            // FEA input concepts (masses / BCs / load scenarios) carried
            // from adapy's deck-write sidecar through the manifest. A baked
            // FEA-result GLB is geometry-only (no ADA_EXT extension), so
            // FemConceptsController's adaExtensionRef parse finds nothing
            // for it — we push the manifest's concepts straight into the
            // store instead, the same way lineage feeds useLineageStore
            // above. This runs after setLoadedSourceName, whose store
            // subscription (reparse → empty extension) would otherwise have
            // just cleared the overlay.
            if (manifest.fem_concepts) {
                const {useFemConceptsStore} = await import("@/state/femConceptsStore");
                const fc = manifest.fem_concepts;
                useFemConceptsStore.getState().setData({
                    masses: fc.masses ?? [],
                    bcs: fc.bcs ?? [],
                    scenarios: fc.scenarios ?? [],
                });
            }
            // FEM node/element sets -> Scene > FEM groups picker. The streaming mesh.glb has no
            // ADA_EXT (where GroupsSection normally reads groups), so feed the manifest groups
            // straight into the scene-info store it renders from. Members (EL{id}/P{id}) resolve
            // against the AFEM element ranges.
            {
                const {useSceneInfoStore} = await import("@/state/sceneInfoStore");
                const mg = manifest.groups ?? [];
                useSceneInfoStore.getState().setAvailableGroups(
                    mg.map((g) => ({
                        name: g.name,
                        members: g.members,
                        type: "simulation" as const,
                        parent_name: sourceName,
                        fe_object_type: g.fe_object_type,
                    })),
                );
            }
        } catch (err) {
            URL.revokeObjectURL(url);
            throw err;
        }

        const scene = sceneRef.current;
        if (!scene) throw new Error("scene not ready");
        // Scope to the loaded GLB root, not the whole scene — a
        // fem_concepts glyph or other overlay mesh would otherwise be
        // picked up as active.mesh and crash applyFieldToMesh.
        const mesh = findFirstMesh(feaRoot ?? scene);
        if (!mesh) throw new Error("loaded GLB has no mesh");
        const basePositions = snapshotBasePositions(mesh.geometry);

        active = {sourceName, manifest, mesh, basePositions};
        // Publish the model bounding box (the CAD path does this in
        // setupModelLoader; the FEA path bypasses it). Without it, features that
        // key off the model centre — section planes, camera-fit — fall back to the
        // world origin, so a new clip plane sits at (0,0,0) instead of the model.
        try {
            mesh.updateWorldMatrix(true, false);
            const worldBox = new THREE.Box3().setFromObject(mesh);
            if (!worldBox.isEmpty()) useModelState.getState().setBoundingBox(worldBox);
        } catch {
            /* best-effort — never break the load over a bbox */
        }
        // Material flags (vertexColors + morphTargets) are flipped on
        // inside applyFieldToMesh so they cover both the array-typed
        // material that prepareLoadedModel installs on
        // CustomBatchedMesh and the plain-material fallback.

        // Beam-solid mesh — optional, only present in manifests baked
        // from SIF sources with section info. Attached as a child of
        // the main mesh so it inherits the FEA root parent and gets
        // disposed alongside the main mesh on scene swap. Visibility
        // is driven by ``beamSolidsVisible`` in feaAnimationStore —
        // default false so the existing line-only render stays the
        // default and a fresh bake doesn't surprise users with the
        // new solid mesh.
        const beamSolidsVisible = useFeaAnimationStore.getState().beamSolidsVisible;
        const beamSolid = await tryLoadBeamSolids(
            fetcher, sourceName, manifest, beamSolidsVisible,
        );
        if (beamSolid) {
            mesh.add(beamSolid.mesh);
            active.beamSolidMesh = beamSolid.mesh;
            active.beamSolidBasePositions = beamSolid.basePositions;

            // No element-edge wireframe over the beam solids.
            //
            // There used to be one, drawn from the AFEG sidecar: the perimeter of
            // each extruded section plus the seams between adjacent beam
            // elements. It is the wrong thing to call a mesh line. A beam element
            // IS a line — two nodes and the span between them — and its mesh line
            // should be that line whether or not the section is drawn around it.
            // Outlining the extrusion instead put a rectangle round every section
            // end and read as mesh that the model does not have.
            //
            // Nothing replaces it, because nothing needs to: the main mesh's edge
            // sidecar already carries one edge per line element (see
            // get_mesh_topology — "Line elements contribute edges but no
            // triangles"), so beams keep exactly the mesh line they have with the
            // sections switched off. The bake still writes beam_solids_edges_url;
            // it is simply no longer consumed.

            // AFBV: per-vertex (node0, node1, t). Required so the

            // solid mesh deforms with the rest of the structure
            // when warp is applied. Best-effort fetch — without it
            // the solid mesh still renders but stays at base
            // positions under any morph scale (the old behaviour).
            if (manifest.mesh.beam_solids_warp_url) {
                try {
                    const warp = await fetchBeamSolidsWarp(
                        fetcher, manifest.mesh.beam_solids_warp_url,
                    );
                    if (warp.n_verts === beamSolid.basePositions.length / 3) {
                        active.beamSolidWarp = warp;
                    } else {
                        // eslint-disable-next-line no-console
                        console.warn(
                            `[fea-streaming] AFBV vertex count ${warp.n_verts} ` +
                            `!= solid mesh vertices ${beamSolid.basePositions.length / 3}; ` +
                            `solid beams won't follow deformation.`,
                        );
                    }
                } catch (err) {
                    // eslint-disable-next-line no-console
                    console.warn("[fea-streaming] failed to load AFBV warp sidecar:", err);
                }
            }
        }

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
                if (active) active.edgeIndices = edgeIndices;

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
                        // Transparent (opacity stays 1 — colour unchanged) so the
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
                    // Clip the element-edge wireframe with the model under section planes.
                    segments.userData.__clipWithModel = true;
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
                    beamSegments.userData.__clipWithModel = true;
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
    }

    stage("loading field data", 0.55);
    throwIfAborted();

    // Resolve the warp source. The picked field drives colour
    // regardless; warp depends on category:
    //   * displacement → warp by self (legacy behaviour).
    //   * reaction → never warp (force vectors aren't a deformation).
    //   * stress / strain / other → warp by the manifest's displacement
    //     field when ``warpEnabled`` is on, else stay undeformed.
    // Step index is shared across fields — almost all analyses use a
    // parallel step structure, so step 3 of the stress field aligns
    // with step 3 of the displacement field. If the displacement field
    // has fewer steps (unusual; happens when a user runs a sub-step
    // displacement output), we clamp to its last step and warn.
    // Field-less FEM meshes (no results) skip all result coloring / warp / step handling —
    // they only need geometry + beam-solids (loaded above). Everything below is field work.
    if (field) {
    const reductionStr = reduction ?? "magnitude"; // field present -> reduction is meaningful
    const warpEnabled = useFeaAnimationStore.getState().warpEnabled;
    const warpInfo = await resolveWarpSource(
        rangeFetcher,
        fetcher,
        cacheKey,
        manifest,
        field,
        stepIndex,
        warpEnabled,
    );

    if (field.per_type && field.per_type.length > 0) {
        // Element-field render path (AFEL). Range-fetch one step per
        // element-type bucket in parallel; the bake guarantees parallel
        // step counts across buckets within a logical field, so the same
        // ``stepIndex`` indexes every bucket. The reduction kernel
        // collapses (n_ips × n_components) → 1 scalar per element and
        // writes vertex colours via AFEM draw ranges.
        const buckets = field.per_type;
        const perTypeStepValues = await Promise.all(
            buckets.map((bk, i) =>
                fetchElemFieldStep(rangeFetcher, fetcher, bk, stepIndex, cacheKey).catch((err) => {
                    throw new Error(
                        `element field ${field.name_canonical} bucket ${buckets[i].elem_type} ` +
                        `step ${stepIndex}: ${err instanceof Error ? err.message : String(err)}`,
                    );
                }),
            ),
        );
        const {layer, ipReduction, nodalAverage} = useFeaAnimationStore.getState();
        applyElemFieldToMesh({
            mesh: active.mesh,
            basePositions: active.basePositions,
            colorField: field,
            perTypeStepValues,
            layer,
            ipReduction,
            reduction: reductionStr,
            warpField: warpInfo?.field,
            warpStepValues: warpInfo?.stepValues,
            displacementScale,
            colormap,
            nodalAverage,
            // Only where the deck cannot show beam solids. Where it can, the beam
            // carries its result on its own surface, and a coloured line as well
            // puts two renderings of one beam in the same place -- the black
            // element-edge overlay against the coloured line, neither legible.
            // Always build them. Which of the two renderings you SEE is a
            // visibility question, not a build-time one -- gating on whether the
            // bake carried solids meant a deck that had them showed black beams
            // the moment you switched the solids off.
            lineFallback: true,
        });
        // Beam-solid mesh — paint with the same AFEL data. Beam
        // labels appear in both drawRanges maps, but the main-mesh
        // entries have zero triangles (line elements) so the kernel
        // is a no-op there for beams, and the beam-solid mesh has no
        // entries for shells. Net effect: each label paints exactly
        // the mesh that owns its triangles. Smooth shading skipped:
        // each beam has at most one IP along its length so per-
        // element colour and nodal-averaged colour coincide.
        //
        // Note: applyElemFieldToMesh installs a zero-magnitude morph
        // delta (no warp arg here). ``installBeamSolidWarp`` below
        // overwrites that with the lerped nodal warp so the solid
        // beams stay connected to the deformed structure under any
        // morph-scale factor.
        if (active.beamSolidMesh && active.beamSolidBasePositions) {
            applyElemFieldToMesh({
                mesh: active.beamSolidMesh,
                basePositions: active.beamSolidBasePositions,
                colorField: field,
                perTypeStepValues,
                layer,
                ipReduction,
                reduction: reductionStr,
                colormap,
                nodalAverage: false,
            });
            if (active.beamSolidWarp) {
                installBeamSolidWarp(
                    active.mesh,
                    active.beamSolidMesh,
                    active.beamSolidBasePositions,
                    active.beamSolidWarp,
                    warpInfo?.field,
                    warpInfo?.stepValues,
                );
            }
        }
    } else {
        const colorStepValues = await fetchFieldStep(rangeFetcher, fetcher, field, stepIndex, cacheKey);

        applyFieldToMesh({
            mesh: active.mesh,
            basePositions: active.basePositions,
            colorField: field,
            colorStepValues,
            reduction: reductionStr,
            warpField: warpInfo?.field,
            warpStepValues: warpInfo?.stepValues,
            displacementScale,
            colormap,
        });

        // Beam-solid mesh: paint it from the same nodal field.
        //
        // This used to switch vertex colours off, on the reasoning that a
        // beam-solid vertex is not an FEA node. True of the vertex, false of the
        // beam: the AFBV sidecar names each vertex's two end nodes and its axial
        // parameter, which is the very interpolation installBeamSolidWarp uses to
        // MOVE that vertex. Anything that can be interpolated to a position can be
        // interpolated to a colour, so a displacement field now paints the beams as
        // well as the shells — as the reference postprocessor does, and as an element field already did
        // here. Base material on a beam that has a value does not read as "no data";
        // it reads as zero.
        //
        // Warp is independent of colour: install the lerped nodal warp so a
        // displacement field flexes the solid beams in lockstep with the rest of the
        // structure. Without it, scaling the morph influence ×100 leaves rigid solid
        // beams at undeformed positions while the shells fly off.
        if (active.beamSolidMesh) {
            const setVc = (mat: THREE.Material, on: boolean) => {
                if ("vertexColors" in mat && (mat as unknown as {vertexColors: boolean}).vertexColors !== on) {
                    (mat as unknown as {vertexColors: boolean}).vertexColors = on;
                    mat.needsUpdate = true;
                }
            };
            let painted = false;
            if (active.beamSolidWarp && active.beamSolidBasePositions) {
                const sourceColors = beamSolidNodalColors(
                    field,
                    colorStepValues,
                    reductionStr,
                    active.beamSolidWarp,
                    colormap,
                    active.basePositions.length / 3,
                );
                if (sourceColors) {
                    const geom = active.beamSolidMesh.geometry;
                    // Through the element-local expansion, if one is cached on this
                    // geometry from an earlier element field. Same reason the morph
                    // goes through it: a buffer sized for the original vertex count
                    // does not fit an expanded geometry.
                    const nSource = active.beamSolidWarp.n_verts;
                    const renderToSource = sourceVertexIndices(geom, nSource);
                    const renderColors = expandSourceTriples(sourceColors, renderToSource);
                    const existing = geom.getAttribute("color");
                    if (existing && existing.count === renderToSource.length && existing.itemSize === 3) {
                        (existing.array as Float32Array).set(renderColors);
                        existing.needsUpdate = true;
                    } else {
                        geom.setAttribute("color", new THREE.BufferAttribute(renderColors, 3));
                    }
                    painted = true;
                }
            }
            const m = active.beamSolidMesh.material;
            if (Array.isArray(m)) m.forEach((mat) => setVc(mat, painted));
            else if (m) setVc(m as THREE.Material, painted);

            if (active.beamSolidWarp && active.beamSolidBasePositions) {
                installBeamSolidWarp(
                    active.mesh,
                    active.beamSolidMesh,
                    active.beamSolidBasePositions,
                    active.beamSolidWarp,
                    warpInfo?.field,
                    warpInfo?.stepValues,
                );
            }
        }
    }
    } // end if (field)

    stage("rendering", 0.9);
    throwIfAborted();

    // Link the edge overlay's morph state to the mesh's so the
    // wireframe tracks deformation. Idempotent: re-running just
    // re-links, which is fine — the references are stable across
    // step changes.
    linkLineMorphToMesh(active.mesh);
    // Same link for the beam-solid mesh's element-edge wireframe so
    // the seams between adjacent beam elements stay attached to the
    // deformed solid mesh under any morph scale.
    if (active.beamSolidMesh) {
        linkLineMorphToMesh(active.beamSolidMesh);
    }

    // Re-apply the undeformed-wireframe preference. It survives loads and step
    // changes, and the ghost has to be rebuilt after one: the base positions it
    // copies belong to the source that was just loaded.
    refreshUndeformedGhost();

    // Re-apply the view preferences a load resets: which beam rendering shows, and
    // whether element edges are drawn. Both outlive the mesh they were set on.
    {
        const s = useFeaAnimationStore.getState();
        if (active.mesh) setResultLineSegmentsVisible(active.mesh, !s.beamSolidsVisible);
        setFeaElementEdgesVisible(s.elementEdgesVisible);
        setFeaResultColorsVisible(s.resultColorsVisible);
    }

    // Register the session with the animation store so
    // SimulationControls renders the deformation-scale slider /
    // play / stop instead of the GLTF-clip controls. Range follows
    // the field's analysis_kind: static = [0, 1] (one-directional),
    // eigen = [-1, +1] (mode shape has no inherent sign).
    const animStore = useFeaAnimationStore.getState();
    animStore.setMesh(active.mesh);
    animStore.setSourceName(sourceName);
    animStore.setManifest(manifest);
    if (field) {
        // Results present -> activate the FEA session (SimulationControls: step slider / field
        // selector / warp). Range follows analysis_kind: static = [0, 1], eigen = [-1, +1].
        animStore.setSessionActive(true);
        const range: [number, number] = field.analysis_kind === "eigen" ? [-1, 1] : [0, 1];
        animStore.setRange(range);
        animStore.setFactor(displacementScale);
        animStore.setStepIndex(stepIndex);
        animStore.setNSteps(field.n_steps);
        // A deformation scale the model can be seen at. Derived from the
        // displacement field and the model size, and only ever applied while the
        // user has not set a scale of their own.
        {
            const geom = active.mesh.geometry;
            // Recompute rather than trust a cached box: a stale one from an
            // earlier state made the derived scale wobble between field
            // switches, and a number that changes on its own is worse than a
            // number that is slightly off. Base positions do not change, so
            // this is the same answer every time.
            geom.computeBoundingBox();
            const size = geom.boundingBox
                ? geom.boundingBox.min.distanceTo(geom.boundingBox.max)
                : 0;
            animStore.applyAutoScaleFactor(
                autoWarpScale(findDisplacementField(manifest), size),
            );
        }
        animStore.setFieldName(fieldName);
        if (reduction != null) animStore.setReduction(reduction);
        animStore.setColormap(colormap);
        const [legendMin, legendMax] = selectedResultRange(field, reduction ?? "magnitude");
        const legendStore = useColorStore.getState();
        legendStore.setMin(legendMin);
        legendStore.setMax(legendMax);
        legendStore.setShowLegend(true);
    } else {
        // Field-less FEM mesh (model only): no results -> NO simulation session, so
        // SimulationControls + the results-only "show in data" action stay hidden. The
        // beam-solids toggle acts on the module-level `active` mesh, not the session, so it
        // still works from the Scene > FEM panel.
        animStore.setSessionActive(false);
        animStore.setFieldName(null);
        animStore.setNSteps(1);
        animStore.setStepIndex(0);
        useColorStore.getState().setShowLegend(false);
    }

    // applyStep closure captures the *current* (sourceName, manifest,
    // fieldName, reduction). SimulationControls calls this when the
    // user drags the step slider — the callback re-runs
    // load_fea_streaming with the updated stepIndex. Re-registering
    // on every apply keeps the closure fresh even when the user
    // changes field / reduction via the SimulationControls dropdowns.
    // Colormap intentionally reads from the store at call time
    // (load_fea_streaming pulls it from useFeaAnimationStore when the
    // arg is omitted) so a colormap change between apply and the next
    // step drag still picks up the latest selection without needing
    // to re-register the callback here.
    if (field) {
        animStore.setApplyStep(async (newStepIndex: number) => {
            await load_fea_streaming({
                sourceName,
                manifest,
                fieldName,
                stepIndex: newStepIndex,
                reduction,
            });
        });
    }

    // Auto-show the SimulationControls panel on first apply so the
    // user doesn't need to find a hidden toggle for a deformation
    // session they just kicked off. Idempotent — re-applying with a
    // panel already open is a no-op. Field-less FEM meshes have nothing
    // to drive there, so leave the panel as-is.
    const generalAnimStore = useAnimationStore.getState();
    if (field && !generalAnimStore.isControlsVisible) {
        generalAnimStore.setIsControlsVisible(true);
    }

    stage("ready", 1.0);
}

/** Toggle entry point: fetch the manifest, pick sensible defaults
 * (first field, default reduction, step 0, factor 1), and run
 * ``load_fea_streaming``. The user then refines via SimulationControls
 * — they no longer have to step through a modal picker.
 *
 * Returns silently on a manifest with no fields; the storage row
 * stays unchecked-but-toggled which the user can interpret as
 * "nothing renderable in this file". */
export async function load_fea_with_defaults(sourceName: string): Promise<void> {
    if (!runtime.isRestMode()) {
        throw new Error("FEA streaming viewer is only available in REST mode");
    }
    const scope = scopeUrlPart(useScopeStore.getState().current);

    // Mirror the FEA bake's queue lifecycle into the global
    // conversion store so the bottom-right ConversionProgress toast
    // shows queue + bake progress for SIF / RMED files the same way
    // it does for CAD-GLB conversions. Without this hook the SIF
    // path is silent: feaManifestPoll only fires its onProgress
    // callback, which by default has no consumer. Store key follows
    // serverPipeline.ts's ``${sourceKey}::${target}`` convention so
    // duplicate keys can't collide with a CAD conversion of the same
    // source (different target).
    const convStore = useConversionStore.getState();
    const storeKey = `${sourceName}::fea`;
    const startedAt = Date.now();
    // Seed the entry as ``queued`` immediately so the toast appears
    // for the gap between click and the first server progress event.
    // The 202 response from feaManifest fills in the real jobId on
    // the next tick.
    convStore.setJob(storeKey, {
        sourceKey: storeKey,
        jobId: "",
        derivedKey: "",
        status: "queued",
        progress: 0,
        stage: "queuing fea bake",
        error: null,
        startedAt,
    });

    // AbortController + store subscription so the user clicking Kill
    // in the toast actually stops the manifest poll. ConversionProgress
    // calls clearJob() after the cancel endpoint resolves; that drops
    // the row from the store, our subscriber fires .abort(), and the
    // poll loop's signal.aborted check throws AbortError on the next
    // tick. Without this the poll keeps ticking every 600 ms and the
    // onProgress callback re-inserts the toast row 600 ms after the
    // user dismissed it (the "flash, comes back" UX bug).
    const controller = new AbortController();
    const unsubscribe = useConversionStore.subscribe((state, prev) => {
        if (prev.jobs[storeKey] && !state.jobs[storeKey]) {
            controller.abort();
        }
    });

    // The toast covers three phases: queue+convert (server-side bake,
    // polled by feaManifest) → mesh-load (client fetches GLB + sidecars)
    // → render (apply field, install warp). We map them into one 0..1
    // progress bar so the user sees uninterrupted motion: the manifest
    // poll fills 0..0.55, the load_fea_streaming stages map into
    // 0.55..1.0. Keeping the row alive through all three is what makes
    // the load survive the user dismissing the storage panel — the
    // async chain itself runs to completion regardless of UI mount
    // state, but only this toast tells the user that.
    const MANIFEST_PROGRESS_CEILING = 0.55;

    let manifest: FeaManifest;
    try {
        manifest = await viewerApi.feaManifest(scope, sourceName, {
            signal: controller.signal,
            onProgress: ({jobId, stage, progress, status}) => {
                // Race guard: if the user cleared the row between
                // .abort() and AbortError actually propagating up the
                // poll loop, don't resurrect it.
                if (!useConversionStore.getState().jobs[storeKey]) return;
                convStore.setJob(storeKey, {
                    sourceKey: storeKey,
                    jobId,
                    derivedKey: "",
                    status,
                    progress: progress * MANIFEST_PROGRESS_CEILING,
                    stage,
                    error: null,
                    startedAt,
                });
            },
        });
        if (!manifest) {
            convStore.clearJob(storeKey);
            return;
        }
        // Fire registered plugin result-sidecar loaders once the FEA geometry is
        // loaded — the FEA path does its own scene setup and bypasses
        // setupModelLoader (the CAD/GLB run-point), so without this a plugin's
        // sidecar (e.g. a code-check result next to the FEA manifest) never loads.
        // Core names no plugin; the fetcher is rooted at the same _derived/<src>.fea/
        // dir the mesh + field blobs come from. Best-effort — never breaks the load.
        const fireResultSidecarLoaders = () => {
            try {
                const {fetcher, rangeFetcher} = makeViewerApiFetcher(scope, sourceName);
                const feaPrefix = `_derived/${sourceName.replace(/^\/+/, "")}.fea/`;
                const sidecar: SidecarFetcher = {
                    url: (rel) => viewerApi.blobUrl(scope, feaPrefix + rel.replace(/^\/+/, "")),
                    json: async (rel) =>
                        JSON.parse(new TextDecoder().decode(new Uint8Array(await fetcher(rel)))),
                    bytes: async (rel, range) =>
                        range ? (await rangeFetcher(rel, range.start, range.end)).buf : fetcher(rel),
                };
                void runResultSidecarLoaders({manifest, fetcher: sidecar, scope, sourceName});
            } catch (err) {
                console.warn("[fea] plugin result-sidecar loaders failed (non-fatal)", err);
            }
        };
        if (!Array.isArray(manifest.fields) || manifest.fields.length === 0) {
            // No result fields — a design-model FEM mesh (.inp/.fem/.med) or a results deck
            // whose nodal output was all filtered out. Load the geometry field-lessly: mesh +
            // beam-solids + selection wiring, no coloring / warp / step animation.
            await load_fea_streaming({
                sourceName,
                manifest,
                fieldName: null,
                stepIndex: 0,
                reduction: null,
                onStage: (stage, progress) => {
                    if (!useConversionStore.getState().jobs[storeKey]) return;
                    const overall =
                        MANIFEST_PROGRESS_CEILING + progress * (1 - MANIFEST_PROGRESS_CEILING);
                    convStore.setJob(storeKey, {
                        sourceKey: storeKey, jobId: "", derivedKey: "", status: "running",
                        progress: overall, stage, error: null, startedAt,
                    });
                },
            });
            convStore.setJob(storeKey, {
                sourceKey: storeKey, jobId: "", derivedKey: "", status: "done",
                progress: 1, stage: "ready", error: null, startedAt,
            });
            fireResultSidecarLoaders();
            return;
        }
        // Prefer ``category === "displacement"`` so a fresh load opens
        // on the deformation field — that's the field most users want
        // to see first, and it's also the warp source for everything
        // else. Falls back to the first renderable field (nodal or
        // element) when the manifest has no displacement (e.g.
        // stress-only output).
        const field =
            manifest.fields.find((f) => f.category === "displacement") ??
            manifest.fields[0] ??
            // A result-less deck (a design-model .fem/.inp/.med bake, or an
            // input deck exported from a SIN) has geometry and no fields at
            // all. Mesh-only is the correct open, not a crash on fields[0].
            null;
        const reduction = field?.default_view?.reduction ?? "magnitude";
        await load_fea_streaming({
            sourceName,
            manifest,
            fieldName: field ? field.name_canonical : null,
            stepIndex: 0,
            reduction: field ? reduction : null,
            displacementScale: 1,
            signal: controller.signal,
            onStage: (stage, progress) => {
                if (!useConversionStore.getState().jobs[storeKey]) return;
                const overall =
                    MANIFEST_PROGRESS_CEILING
                    + progress * (1 - MANIFEST_PROGRESS_CEILING);
                convStore.setJob(storeKey, {
                    sourceKey: storeKey,
                    jobId: "",
                    derivedKey: "",
                    status: "running",
                    progress: overall,
                    stage,
                    error: null,
                    startedAt,
                });
            },
        });
        // Mark done so the toast self-removes (ConversionProgress
        // filters out done jobs). All three phases completed.
        convStore.setJob(storeKey, {
            sourceKey: storeKey,
            jobId: "",
            derivedKey: "",
            status: "done",
            progress: 1,
            stage: "ready",
            error: null,
            startedAt,
        });
        fireResultSidecarLoaders();
    } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
            // User cancelled (or server-side cancel via the kill
            // endpoint). The store row is already gone by the time
            // we get here; don't surface an error toast for an
            // explicitly-requested abort.
            return;
        }
        const msg = err instanceof Error ? err.message : String(err);
        convStore.setJob(storeKey, {
            sourceKey: storeKey,
            jobId: "",
            derivedKey: "",
            status: "error",
            progress: 0,
            stage: "failed",
            error: msg,
            startedAt,
        });
        throw err;
    } finally {
        unsubscribe();
    }
}

/** Wire the LineSegments wireframe child to share morph attributes
 * + influences with the parent mesh, so changing
 * mesh.morphTargetInfluences[0] morphs both. */
function linkLineMorphToMesh(mesh: THREE.Mesh): void {
    for (const child of mesh.children) {
        if (!(child instanceof THREE.LineSegments)) continue;
        const lineGeom = child.geometry as THREE.BufferGeometry;
        // A child that brought its own morph keeps it. The element-edge overlay
        // SHARES the parent's position buffer, so the parent's per-vertex deltas
        // are exactly what it needs. The result-line renderer does not: it has
        // two vertices per beam in its own buffer and its own deltas to match.
        // Forcing the parent's array onto it hands it deltas of the wrong length
        // read against the wrong vertices, which is why the coloured beams drifted
        // away from the black outline instead of moving with it.
        if (
            lineGeom.morphAttributes.position
            && lineGeom.morphAttributes.position !== mesh.geometry.morphAttributes.position
        ) continue;
        // morphAttributes is per-geometry; sharing the same array of
        // BufferAttributes makes both geometries reference the same
        // morph delta data on the GPU.
        if (mesh.geometry.morphAttributes.position) {
            lineGeom.morphAttributes.position = mesh.geometry.morphAttributes.position;
            lineGeom.morphTargetsRelative = mesh.geometry.morphTargetsRelative;
        }
        // morphTargetInfluences is per-Object3D; sharing the same
        // array reference means writes through mesh.morphTargetInfluences
        // are visible to the line too.
        if (mesh.morphTargetInfluences) {
            child.morphTargetInfluences = mesh.morphTargetInfluences;
            child.morphTargetDictionary = mesh.morphTargetDictionary ?? undefined;
        }
        const mat = child.material as THREE.LineBasicMaterial;
        if (mat && "morphTargets" in mat) {
            (mat as any).morphTargets = true;
            mat.needsUpdate = true;
        }
        // Mirror the morph-texture rebuild that applyFieldToMesh does
        // for the parent mesh. lineGeom shares the parent's position
        // BufferAttribute, so when applyField dispatched 'dispose' on
        // mesh.geometry, three.js's WebGLAttributes deleted the GPU
        // buffer for that shared position. lineGeom's VAO still
        // references the (now-orphaned) buffer ID, which is why the
        // wireframe vanishes after a step change. Dispatching dispose
        // here rebuilds lineGeom's VAO + morph texture against the
        // freshly-uploaded position buffer on the next render. No-op
        // on the first call (no renderer state yet).
        lineGeom.dispatchEvent({type: "dispose"});
    }
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
    if (!active?.mesh) return;
    const show = useFeaAnimationStore.getState().showUndeformed;
    if (!show || !active.edgeIndices || active.edgeIndices.length === 0) {
        clearUndeformedGhost(active.mesh);
        if (active.beamSolidMesh) clearUndeformedGhost(active.beamSolidMesh);
        requestRender();
        return;
    }
    installUndeformedGhost(active.mesh, active.basePositions, active.edgeIndices);
    requestRender();
}

/** Set the preference and apply it in one call — what a toolbar toggle wants. */
export function setFeaUndeformedGhost(show: boolean): void {
    useFeaAnimationStore.getState().setShowUndeformed(show);
    refreshUndeformedGhost();
}
