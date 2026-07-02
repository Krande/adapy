import * as THREE from "three";

import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {runtime} from "@/runtime/config";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";

import {cacheAndBuildTree} from "@/state/model_worker/cacheModelUtils";
import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {fetchFieldStep, makeViewerApiFetcher} from "@/services/feaFieldBlob";
import {validateCapacityResults} from "@/services/capacityResults";
import type {FeaFetcher, FeaRangeFetcher} from "@/services/fea/feaFetcher";
import {fetchBeamSolidsWarp, ParsedBeamSolidsWarp} from "@/services/feaBeamSolidsWarp";
import {fetchMeshEdges} from "@/services/feaMeshEdges";
import {fetchMeshElements, MeshElementEntry} from "@/services/feaMeshElements";
import {convert_to_custom_batch_mesh} from "@/utils/scene/convert_to_custom_batch_mesh";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {
    CapacityManifest,
    FeaManifest,
    FeaManifestField,
    FeaManifestFieldPerType,
    ScopeUrl,
    viewerApi,
} from "@/services/viewerApi";
import {sceneRef} from "@/state/refs";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useCapacityResultsStore, WORST_CASE_ID} from "@/state/capacityResultsStore";
import type {CapacityResults, CapacityWorstSummary} from "@/state/capacityResultsStore";
import {useConversionStore} from "@/state/conversionStore";
import {usePerfStore, requestRender} from "@/state/perfStore";
import {applyFieldToMesh} from "../fea/applyField";
import {applyElemFieldToMesh} from "../fea/applyElemField";
import {resetFeaAnimationPhase} from "../fea/feaAnimationDriver";
import {clearGoToNode} from "../fea/goToNode";
import {useTableNavStore} from "@/state/tableNavStore";
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
    beamSolidEdges?: THREE.LineSegments;
    /** Whole-model element-edge wireframe (AFEG). Retained so the capacity
     *  "Only definitions" view can hide it (and the "Show rest as wireframe"
     *  toggle can bring it back) without disturbing the face meshes. */
    feaEdges?: THREE.LineSegments;
    /** Capacity-model boundary overlay built from AFEM element draw ranges. */
    capacityBoundaryOverlay?: THREE.LineSegments;
    /** Amber polyline marking the girder line itself (girder-run models),
     *  drawn through each girder model's station points. */
    capacityGirderLineOverlay?: THREE.LineSegments;
    /** Dashed near-black polylines marking the stiffeners of the panel
     *  capacity models (station-point lines). */
    capacityStiffenerLineOverlay?: THREE.LineSegments;
    /** Red boundary overlay for the selected capacity model. */
    capacitySelectedBoundaryOverlay?: THREE.LineSegments;
    /** Amber boundary overlay for the individually selected stiffener strip
     *  within the selected capacity model. */
    capacitySelectedStripOverlay?: THREE.LineSegments;
    /** Non-pickable non-indexed overlay used for hard-edged capacity colors. */
    capacityColorOverlay?: THREE.Mesh;
    /** Capacity color overlay for optional beam-solid geometry. */
    beamSolidCapacityColorOverlay?: THREE.Mesh;
    /** Pre-isolation hidden-range snapshot per mesh, so "show only
     *  definitions" can restore exactly what was visible when it was
     *  switched on. Present only while isolation is active. */
    capacityIsolationSaved?: {main?: Set<string>; beam?: Set<string>};
    /** Numbered marker sprites at the 3 Section-5 stations of the selected
     *  stiffener (positions 1/2/3 of the resolved design stresses). */
    capacityStationsGroup?: THREE.Group;
    /** Fetchers retained so the capacity overlay can Range-fetch AFEL colour
     *  blobs and lazy-load per-case detail after the initial load, using the
     *  same manifest-relative resolver as the FEA artefacts (capacity files are
     *  co-located in the artefact dir). */
    capacityFetch?: {
        fetcher: FeaFetcher;
        rangeFetcher: FeaRangeFetcher;
        cacheKey: string;
    };
}

// Per-position marker colours (positions 1/2/3). Keep in sync with the capacity
// side panel (CapacityControls STATION_COLORS) so the dots match the markers.
const CAPACITY_STATION_COLORS = ["#38bdf8", "#fbbf24", "#fb7185"];

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
    if (active?.beamSolidEdges) {
        // The wireframe lives as a child of beamSolidMesh, so it would
        // inherit ancestor invisibility, but ``mesh.visible = false``
        // does not propagate through three's render walk by itself for
        // children added to a non-Mesh group. Setting it directly is
        // both belt-and-braces and lets future refactors detach the
        // wireframe to a sibling without losing the link.
        active.beamSolidEdges.visible = visible;
    }
}

export function clearActiveFeaStreaming(): void {
    active = null;
    clearCapacityStepCache();
    useFeaAnimationStore.getState().reset();
    useCapacityResultsStore.getState().clear();
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
        const n0 = warp.node0;
        const n1 = warp.node1;
        const ts = warp.t;
        for (let v = 0; v < nVerts; v++) {
            const t = ts[v];
            const a = n0[v] * nc;
            const b = n1[v] * nc;
            const out = v * 3;
            // Pre-fetch up to first 3 components per endpoint; treat
            // missing components as zero (1D / 2D displacement fields
            // shouldn't appear today, but defensive).
            const ax = warpStepValues[a] || 0;
            const ay = nc >= 2 ? warpStepValues[a + 1] || 0 : 0;
            const az = nc >= 3 ? warpStepValues[a + 2] || 0 : 0;
            const bx = warpStepValues[b] || 0;
            const by = nc >= 2 ? warpStepValues[b + 1] || 0 : 0;
            const bz = nc >= 3 ? warpStepValues[b + 2] || 0 : 0;
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
    const posAttr = geom.getAttribute("position");
    if (posAttr) {
        (posAttr.array as Float32Array).set(basePositions);
        posAttr.needsUpdate = true;
    }
    geom.morphAttributes.position = [new THREE.BufferAttribute(displacement, 3)];
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

async function loadCapacityResultsIfPresent(
    fetcher: FeaFetcher,
    scope: ScopeUrl,
    sourceName: string,
    manifest: FeaManifest,
): Promise<void> {
    const capacity = await resolveCapacityManifest(fetcher, scope, sourceName, manifest);
    const store = useCapacityResultsStore.getState();
    if (!capacity?.results_url) {
        store.clear();
        return;
    }
    if (
        store.source?.sourceName === sourceName
        && store.source.resultsUrl === capacity.results_url
        && store.results
    ) {
        return;
    }

    store.setLoading(true);
    try {
        const buf = await fetchCapacityBuffer(fetcher, scope, capacity.results_url);
        const text = new TextDecoder("utf-8").decode(buf);
        const results = JSON.parse(text) as CapacityResults;
        validateCapacityResults(results, {manifest});
        useCapacityResultsStore.getState().setCapacityData(
            capacity,
            {sourceName, resultsUrl: capacity.results_url},
            results,
        );
    } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        useCapacityResultsStore.getState().setError(message);
        // eslint-disable-next-line no-console
        console.warn("[fea-streaming] failed to load capacity results:", err);
    }
}

async function resolveCapacityManifest(
    fetcher: FeaFetcher,
    scope: ScopeUrl,
    sourceName: string,
    manifest: FeaManifest,
): Promise<CapacityManifest | null> {
    if (manifest.capacity?.results_url) return manifest.capacity;

    const sourcePath = sourceName.replace(/^\/+/, "");
    const slash = sourcePath.lastIndexOf("/");
    const dir = slash >= 0 ? sourcePath.slice(0, slash + 1) : "";
    const filename = slash >= 0 ? sourcePath.slice(slash + 1) : sourcePath;
    const stem = filename.replace(/\.[^.]+$/, "");

    for (const candidate of ["capacity.results.json"]) {
        try {
            await fetcher(candidate);
            return {version: 1, results_url: candidate, field_strategy: "json-auto"};
        } catch {
            // Missing auto-discovery candidates are expected.
        }
    }

    for (const candidate of [
        `${dir}${stem}.c201.json`,
        `${dir}${stem}.capacity.json`,
        `${dir}capacity.results.json`,
    ]) {
        try {
            await viewerApi.getBlob(scope, candidate);
            return {version: 1, results_url: `scope://${candidate}`, field_strategy: "json-auto"};
        } catch {
            // Missing auto-discovery candidates are expected.
        }
    }
    return null;
}

function fetchCapacityBuffer(
    fetcher: FeaFetcher,
    scope: ScopeUrl,
    resultsUrl: string,
): Promise<ArrayBuffer> {
    if (resultsUrl.startsWith("scope://")) {
        return viewerApi.getBlob(scope, resultsUrl.slice("scope://".length));
    }
    return fetcher(resultsUrl);
}

export async function applyCapacityVisualField(
    metricId?: string,
    caseId?: string,
): Promise<boolean> {
    if (!active?.mesh) return false;
    const store = useCapacityResultsStore.getState();
    const results = store.results;
    if (!results) return false;
    const run = results.runs.find((r) => r.id === store.activeRunId) ?? results.runs[0];
    if (!run) return false;
    const fieldId = metricId ?? store.activeMetricId;
    const activeCaseId =
        caseId
        ?? store.activeCaseId
        ?? run.result_cases?.[0]?.id
        ?? run.field_case_steps?.[0]
        ?? run.case_results?.[0]?.case_id;
    if (!activeCaseId) return false;
    const field = run.visual_fields.find((f) => f.id === fieldId);
    if (!field) return false;

    let values: Array<{element_id: number; value: number | null}> | null = null;
    if (activeCaseId === WORST_CASE_ID) {
        // Worst over the user-selected case subset (max UF per element).
        if (field.storage === "afel") {
            values = await fetchAfelWorstValues(run, field, selectedWorstCaseIds(run));
        }
    } else if (field.storage === "afel") {
        values = await fetchAfelCapacityValues(run, field, activeCaseId);
    } else if (field.storage === "json") {
        // Legacy (<=v5) inline values.
        values = field.cases?.find((c) => c.case_id === activeCaseId)?.values ?? null;
    }
    if (!values) return false;
    // The store may have moved on while we awaited the Range fetch (user clicked
    // a different case/metric). Both call sites paint the *active* selection, so
    // drop the paint if it no longer matches what is selected now.
    if (!active?.mesh) return false;
    const now = useCapacityResultsStore.getState();
    if (now.activeCaseId !== activeCaseId || now.activeMetricId !== fieldId) {
        return false;
    }

    paintCapacityEntries(active.mesh, values);
    if (active.beamSolidMesh) {
        paintCapacityEntries(active.beamSolidMesh, values);
    }
    applyCapacitySelectionHighlight();
    requestRender();
    return true;
}

// Cache decoded AFEL (field, case) steps so re-selecting a case repaints
// instantly. Keyed by source + blob url + step. Cleared on scene swap via the
// underlying ELEM_*_CACHE in feaElemFieldBlob (whole-blob) plus this map.
const CAPACITY_STEP_CACHE = new Map<string, Array<{element_id: number; value: number | null}>>();

function clearCapacityStepCache(): void {
    CAPACITY_STEP_CACHE.clear();
}

/** Range-fetch one (field, case) step of an AFEL capacity colour blob and map it
 *  to ``{element_id, value}`` paint entries via the run's shared element axis.
 *  Returns ``null`` when the fetch context, axis, or step is unavailable. */
async function fetchAfelCapacityValues(
    run: CapacityResults["runs"][number],
    field: CapacityResults["runs"][number]["visual_fields"][number],
    caseId: string,
): Promise<Array<{element_id: number; value: number | null}> | null> {
    const ctx = active?.capacityFetch;
    const labels = run.element_axis;
    const steps = run.field_case_steps ?? run.result_cases.map((c) => c.id);
    if (!ctx || !labels?.length || !field.blob_url) return null;
    const stepIndex = steps.indexOf(caseId);
    if (stepIndex < 0) return null;

    const cacheKey = `${active?.sourceName ?? ""}::${field.blob_url}::${stepIndex}`;
    const cached = CAPACITY_STEP_CACHE.get(cacheKey);
    if (cached) return cached;

    // Reuse the AFEL single-step Range fetch the simulation element-field path
    // uses. n_ips = n_components = 1, so a step is one float per element.
    const bucket: FeaManifestFieldPerType = {
        elem_type: "capacity",
        n_elements: labels.length,
        n_ips: 1,
        ip_layout: [],
        element_labels: labels,
        blob: {
            url: field.blob_url,
            header_bytes: field.header_bytes ?? 1024,
            stride_bytes: field.stride_bytes ?? labels.length * 4,
            dtype: field.dtype ?? "float32",
            byte_order: (field.byte_order as "little" | "big") ?? "little",
        },
        scalar_range: {},
    };
    let step: Float32Array;
    try {
        step = await fetchElemFieldStep(ctx.rangeFetcher, ctx.fetcher, bucket, stepIndex, ctx.cacheKey);
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn(`[capacity] failed to fetch AFEL step for ${field.blob_url}:`, err);
        return null;
    }
    const out: Array<{element_id: number; value: number | null}> = [];
    for (let i = 0; i < labels.length; i++) {
        const v = step[i];
        if (Number.isFinite(v)) out.push({element_id: labels[i], value: v});
    }
    CAPACITY_STEP_CACHE.set(cacheKey, out);
    return out;
}

/** The worst-view case subset, intersected with the run's actual cases. An
 *  empty selection means "no cases" (the user unchecked them all). */
function selectedWorstCaseIds(run: CapacityResults["runs"][number]): string[] {
    const all = new Set(run.field_case_steps ?? run.result_cases.map((c) => c.id));
    const selected = useCapacityResultsStore.getState().worstCaseIds ?? [];
    return selected.filter((id) => all.has(id));
}

/** Per-element worst (max) UF over a set of cases for one AFEL field. Fetches
 *  each case's step (Range, ~34 KB, cached) and reduces element-wise. */
async function fetchAfelWorstValues(
    run: CapacityResults["runs"][number],
    field: CapacityResults["runs"][number]["visual_fields"][number],
    caseIds: string[],
): Promise<Array<{element_id: number; value: number | null}> | null> {
    if (caseIds.length === 0) return [];
    const labels = run.element_axis;
    if (!labels?.length) return null;
    const rowOf = new Map<number, number>();
    for (let i = 0; i < labels.length; i++) rowOf.set(labels[i], i);

    const best = new Float32Array(labels.length).fill(Number.NEGATIVE_INFINITY);
    for (const caseId of caseIds) {
        const perCase = await fetchAfelCapacityValues(run, field, caseId);
        if (!perCase) continue;
        for (const entry of perCase) {
            if (entry.value == null) continue;
            const row = rowOf.get(entry.element_id);
            if (row !== undefined && entry.value > best[row]) best[row] = entry.value;
        }
    }
    const out: Array<{element_id: number; value: number | null}> = [];
    for (let i = 0; i < labels.length; i++) {
        if (Number.isFinite(best[i])) out.push({element_id: labels[i], value: best[i]});
    }
    return out;
}

/** Lazy-load the compact worst-over-cases summary into the capacity store. */
export async function loadCapacityWorstSummary(): Promise<void> {
    const ctx = active?.capacityFetch;
    if (!ctx) return;
    const store = useCapacityResultsStore.getState();
    const results = store.results;
    if (!results) return;
    const run = results.runs.find((r) => r.id === store.activeRunId) ?? results.runs[0];
    const url = run?.worst_summary_url;
    if (!url) return;
    if (store.worstSummary || store.worstSummaryLoading) return;

    store.setWorstSummaryLoading(true);
    try {
        const buf = await ctx.fetcher(url);
        const summary = JSON.parse(new TextDecoder("utf-8").decode(buf)) as CapacityWorstSummary;
        useCapacityResultsStore.getState().setWorstSummary(summary);
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn("[capacity] failed to load worst summary:", err);
        useCapacityResultsStore.getState().setWorstSummary(null);
    }
}

/** Lazy-load one result case's verbose detail rows into the capacity store.
 *  Idempotent: a no-op when the case is already loaded or in flight. Resolves
 *  the per-case file via the run's ``case_detail.url_template``. */
export async function loadCapacityCaseDetail(caseId: string): Promise<void> {
    const ctx = active?.capacityFetch;
    if (!ctx) return;
    const store = useCapacityResultsStore.getState();
    const results = store.results;
    if (!results) return;
    const run = results.runs.find((r) => r.id === store.activeRunId) ?? results.runs[0];
    const template = run?.case_detail?.url_template;
    if (!run || !template) return;
    if (store.caseDetail[caseId] || store.caseDetailLoading[caseId]) return;

    store.setCaseDetailLoading(caseId, true);
    try {
        const url = template.replace("{case}", encodeURIComponent(caseId));
        const buf = await ctx.fetcher(url);
        const payload = JSON.parse(new TextDecoder("utf-8").decode(buf)) as {
            case_results?: CapacityResults["runs"][number]["case_results"];
        };
        useCapacityResultsStore.getState().setCaseDetail(caseId, payload.case_results ?? []);
    } catch (err) {
        // eslint-disable-next-line no-console
        console.warn(`[capacity] failed to load case detail for ${caseId}:`, err);
        useCapacityResultsStore.getState().setCaseDetail(caseId, []);
    }
}

/** Colour elements by explicit per-element UF values (rest neutral) — used for
 *  the "individual UF" view, where each stiffener's own line + tributary plate
 *  strip is coloured by that stiffener's UF, revealing the within-panel
 *  variation instead of the panel-governing maximum. */
export function applyCapacityIndividualField(
    values: Array<{element_id: number; value: number | null}>,
): boolean {
    if (!active?.mesh) return false;
    paintCapacityEntries(active.mesh, values);
    if (active.beamSolidMesh) {
        paintCapacityEntries(active.beamSolidMesh, values);
    }
    applyCapacitySelectionHighlight();
    return true;
}

export function applyCapacityDefinitionView(): boolean {
    if (!active?.mesh) return false;
    const store = useCapacityResultsStore.getState();
    const results = store.results;
    if (!results) return false;
    const run = results.runs.find((r) => r.id === store.activeRunId) ?? results.runs[0];
    if (!run) return false;

    // Girder-run models clutter the scene when every (adjacent) rectangle is
    // outlined; draw the boundary only for the selected girder — its
    // associated tributary plates — while the girder lines mark every model.
    const boundaryModels = run.capacity_models.filter(
        (model) =>
            (model as {type?: string}).type !== "girder" ||
            model.id === store.selectedModelId,
    );
    rebuildCapacityBoundaryOverlay(
        boundaryModels.map((model) => ({
            id: model.id,
            panel_group: model.panel_group,
            element_ids: model.element_ids,
        })),
        capacityFailedModelIds(run, store),
    );
    rebuildCapacityGirderLineOverlay(run.capacity_models, lastGirderUf);
    rebuildCapacityStiffenerLineOverlay(run.capacity_models);
    applyCapacitySelectionHighlight();
    return true;
}

/** Distinct colour for the girder line itself in the definitions view. */
const CAPACITY_GIRDER_LINE_COLOR = 0xf59e0b; // amber
/** Dashed stiffener-line colour in the definitions view. */
const CAPACITY_STIFFENER_LINE_COLOR = 0x111827; // near-black

/** Last per-girder UF values applied (kept so the definition rebuild keeps
 *  the result colours instead of flashing back to amber). */
let lastGirderUf: Map<string, number | null> | null = null;

/** Colour the girder lines by per-model UF (girder run, results on). Pass
 *  ``null`` to return to the neutral amber definition colour. */
export function applyCapacityGirderLineUf(values: Map<string, number | null> | null): void {
    lastGirderUf = values;
    const store = useCapacityResultsStore.getState();
    const run =
        store.results?.runs.find((r) => r.id === store.activeRunId) ??
        store.results?.runs[0];
    if (!run) return;
    rebuildCapacityGirderLineOverlay(run.capacity_models, lastGirderUf);
}

/** Girder-run models mark the girder member itself: a polyline through the
 *  model's station points (start/mid/end of the bay — the girder line). Amber
 *  in the definitions view; coloured by the girder's UF when result values are
 *  supplied. Panels have no such marker; the white boundary outline is theirs. */
function rebuildCapacityGirderLineOverlay(
    models: Array<{id: string; type?: string; stiffeners?: Array<Record<string, unknown>>}>,
    ufByModelId?: Map<string, number | null> | null,
): void {
    disposeCapacityGirderLineOverlay();
    if (!active?.mesh) return;
    const positions: number[] = [];
    const colors: number[] = [];
    const amber = new THREE.Color(CAPACITY_GIRDER_LINE_COLOR);
    const band = new Float32Array(3);
    for (const model of models) {
        if (model.type !== "girder") continue;
        const uf = ufByModelId?.get(model.id);
        let r = amber.r;
        let g = amber.g;
        let b = amber.b;
        if (uf != null && isFinite(uf)) {
            capacityUfColor(uf, band);
            [r, g, b] = band;
        }
        for (const stiff of model.stiffeners ?? []) {
            const stations = stiff.stations as number[][] | undefined;
            if (!stations || stations.length < 2) continue;
            for (let i = 0; i + 1 < stations.length; i++) {
                positions.push(...stations[i].slice(0, 3), ...stations[i + 1].slice(0, 3));
                colors.push(r, g, b, r, g, b);
            }
        }
    }
    if (positions.length === 0) return;
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(positions), 3));
    geom.setAttribute("color", new THREE.BufferAttribute(new Float32Array(colors), 3));
    const mat = new THREE.LineBasicMaterial({
        vertexColors: true,
        depthTest: false,
        depthWrite: false,
        transparent: true,
        opacity: 1.0,
    });
    const overlay = new THREE.LineSegments(geom, mat);
    overlay.name = "capacity-girder-lines";
    overlay.layers.mask = active.mesh.layers.mask;
    overlay.renderOrder = 7; // above the boundary overlay
    active.mesh.add(overlay);
    active.capacityGirderLineOverlay = overlay;
}

function disposeCapacityGirderLineOverlay(): void {
    if (!active?.capacityGirderLineOverlay) return;
    active.capacityGirderLineOverlay.removeFromParent();
    active.capacityGirderLineOverlay.geometry.dispose();
    const material = active.capacityGirderLineOverlay.material;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material.dispose();
    active.capacityGirderLineOverlay = undefined;
}

/** Dashed near-black polylines indicating the stiffeners of the capacity
 *  models: each panel stiffener's station line, and — for girder models — the
 *  supported-stiffener lines carried in ``stiffener_stations`` (the girder's
 *  own ``stiffeners[0]`` is the amber girder line, not a stiffener). */
function rebuildCapacityStiffenerLineOverlay(
    models: Array<{
        type?: string;
        stiffeners?: Array<Record<string, unknown>>;
        stiffener_stations?: number[][][];
    }>,
): void {
    disposeCapacityStiffenerLineOverlay();
    if (!active?.mesh) return;
    const positions: number[] = [];
    const pushPolyline = (stations: number[][] | undefined): void => {
        if (!stations || stations.length < 2) return;
        for (let i = 0; i + 1 < stations.length; i++) {
            positions.push(...stations[i].slice(0, 3), ...stations[i + 1].slice(0, 3));
        }
    };
    for (const model of models) {
        if (model.type === "girder") {
            for (const line of model.stiffener_stations ?? []) pushPolyline(line);
            continue;
        }
        for (const stiff of model.stiffeners ?? []) {
            pushPolyline(stiff.stations as number[][] | undefined);
        }
    }
    if (positions.length === 0) return;
    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(positions), 3));
    const mat = new THREE.LineDashedMaterial({
        color: CAPACITY_STIFFENER_LINE_COLOR,
        dashSize: 0.12,
        gapSize: 0.08,
        depthTest: true,
        depthWrite: false,
        transparent: true,
        opacity: 0.9,
    });
    const overlay = new THREE.LineSegments(geom, mat);
    overlay.computeLineDistances(); // required for dashed rendering
    overlay.name = "capacity-stiffener-lines";
    overlay.layers.mask = active.mesh.layers.mask;
    overlay.renderOrder = 4;
    active.mesh.add(overlay);
    active.capacityStiffenerLineOverlay = overlay;
}

function disposeCapacityStiffenerLineOverlay(): void {
    if (!active?.capacityStiffenerLineOverlay) return;
    active.capacityStiffenerLineOverlay.removeFromParent();
    active.capacityStiffenerLineOverlay.geometry.dispose();
    const material = active.capacityStiffenerLineOverlay.material;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material.dispose();
    active.capacityStiffenerLineOverlay = undefined;
}

/** Capacity models whose check raised (and was skipped) in the current case
 *  context — a specific case, or the union of the selected cases in the worst
 *  view. Their definition edges are painted red so the failure is visible in
 *  the 3D scene, matching the red error banner in the Capacity sidecar. */
function capacityFailedModelIds(
    run: {errors?: Array<{capacity_model_id: string; case_id: string}>},
    store: {activeCaseId: string | null; worstCaseIds: string[]},
): Set<string> {
    const out = new Set<string>();
    const errors = run.errors ?? [];
    if (errors.length === 0) return out;
    let cases: Set<string> | null;
    if (store.activeCaseId === WORST_CASE_ID) {
        cases = new Set(store.worstCaseIds);
    } else if (store.activeCaseId) {
        cases = new Set([store.activeCaseId]);
    } else {
        cases = null; // no case context → flag any error
    }
    for (const err of errors) {
        if (cases === null || cases.has(err.case_id)) out.add(err.capacity_model_id);
    }
    return out;
}

export function clearCapacityDefinitionView(): void {
    disposeCapacityBoundaryOverlay();
    disposeCapacityGirderLineOverlay();
    disposeCapacityStiffenerLineOverlay();
    clearCapacitySelectionHighlight();
}

export function clearCapacityVisualField(): void {
    if (!active) return;
    disposeCapacityColorOverlay("main");
    disposeCapacityColorOverlay("beam");
}

/** "Show only definitions" — hide every FEA draw range that is not part of a
 *  capacity model, leaving just the capacity panels (their faces, plus the
 *  boundary/colour overlays when those are on). The pre-isolation hidden set is
 *  snapshotted so {@link clearCapacityIsolation} restores exactly what the user
 *  had visible. Idempotent: re-applies cleanly when the run/models change. */
export function applyCapacityIsolation(): boolean {
    if (!active?.mesh) return false;
    const store = useCapacityResultsStore.getState();
    const results = store.results;
    if (!results) return false;
    const run = results.runs.find((r) => r.id === store.activeRunId) ?? results.runs[0];
    if (!run) return false;

    const keep = new Set<string>();
    for (const model of run.capacity_models) {
        for (const elementId of model.element_ids.all ?? []) keep.add(`E${elementId}`);
    }

    active.capacityIsolationSaved = active.capacityIsolationSaved ?? {};
    isolateMeshToRanges(active.mesh, keep, "main");
    if (active.beamSolidMesh) isolateMeshToRanges(active.beamSolidMesh, keep, "beam");
    requestRender();
    return true;
}

function isolateMeshToRanges(mesh: THREE.Mesh, keep: Set<string>, slot: "main" | "beam"): void {
    if (!(mesh instanceof CustomBatchedMesh) || !active?.capacityIsolationSaved) return;
    const saved = active.capacityIsolationSaved;
    // Snapshot the user's pre-isolation hidden ranges once, so toggling off
    // restores them rather than blindly un-hiding everything.
    if (saved[slot] === undefined) saved[slot] = new Set(mesh.getHiddenRanges());
    const baseline = saved[slot]!;

    // Reset to the snapshot baseline, then hide everything outside `keep`.
    mesh.unhideAllDrawRanges();
    const toHide: string[] = [];
    for (const id of mesh.drawRanges.keys()) {
        if (!keep.has(id) || baseline.has(id)) toHide.push(id);
    }
    if (toHide.length) mesh.hideBatchDrawRange(toHide);
}

/** Show numbered markers (1/2/3) at the stiffener's Section-5 stations.
 *  ``points`` are [start, mid, end] coordinates in the mesh (SIN) frame; the
 *  sprites are added as children of the FEA mesh so they share its transform. */
export function applyCapacityStations(points: number[][] | null | undefined): boolean {
    clearCapacityStations();
    if (!active?.mesh || !points || points.length === 0) return false;
    const group = new THREE.Group();
    group.name = "capacity-stations";
    points.slice(0, 3).forEach((p, i) => {
        if (!p || p.length < 3) return;
        const sprite = makeStationSprite(String(i + 1), CAPACITY_STATION_COLORS[i] ?? "#38bdf8");
        sprite.position.set(p[0], p[1], p[2]);
        sprite.raycast = () => undefined;
        group.add(sprite);
    });
    if (group.children.length === 0) return false;
    active.mesh.add(group);
    active.capacityStationsGroup = group;
    requestRender();
    return true;
}

export function clearCapacityStations(): void {
    if (!active?.capacityStationsGroup) return;
    const group = active.capacityStationsGroup;
    group.removeFromParent();
    group.traverse((obj) => {
        if (obj instanceof THREE.Sprite) {
            obj.material.map?.dispose();
            obj.material.dispose();
        }
    });
    active.capacityStationsGroup = undefined;
    requestRender();
}

function makeStationSprite(label: string, color: string): THREE.Sprite {
    const size = 64;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = size;
    const ctx = canvas.getContext("2d") as CanvasRenderingContext2D;
    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2 - 5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = 5;
    ctx.strokeStyle = "#0f172a";
    ctx.stroke();
    ctx.fillStyle = "#0f172a";
    ctx.font = "bold 40px sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, size / 2, size / 2 + 2);

    const texture = new THREE.CanvasTexture(canvas);
    texture.minFilter = THREE.LinearFilter;
    const material = new THREE.SpriteMaterial({
        map: texture,
        sizeAttenuation: false,
        depthTest: false,
        depthWrite: false,
        transparent: true,
    });
    const sprite = new THREE.Sprite(material);
    // sizeAttenuation:false → constant screen size; ~5% of viewport height.
    sprite.scale.set(0.05, 0.05, 1);
    sprite.renderOrder = 12;
    return sprite;
}

/** Undo {@link applyCapacityIsolation}: restore the snapshotted hidden set. */
export function clearCapacityIsolation(): void {
    if (!active?.capacityIsolationSaved) return;
    const saved = active.capacityIsolationSaved;
    restoreMeshHidden(active.mesh, saved.main);
    if (active.beamSolidMesh) restoreMeshHidden(active.beamSolidMesh, saved.beam);
    active.capacityIsolationSaved = undefined;
    requestRender();
}

function restoreMeshHidden(mesh: THREE.Mesh | undefined, savedHidden: Set<string> | undefined): void {
    if (!(mesh instanceof CustomBatchedMesh)) return;
    mesh.unhideAllDrawRanges();
    if (savedHidden && savedHidden.size) mesh.hideBatchDrawRange(savedHidden);
}

export function applyCapacitySelectionHighlight(): void {
    if (!active?.mesh) return;
    const store = useCapacityResultsStore.getState();
    const results = store.results;
    if (!results) return;
    const run = results.runs.find((r) => r.id === store.activeRunId) ?? results.runs[0];
    if (!run) return;
    if (!store.showDefinitions) {
        updateCapacitySelectionOverlay(run, null);
        return;
    }
    updateCapacitySelectionOverlay(run, store.selectedModelId);
}

function clearCapacitySelectionHighlight(): void {
    disposeCapacitySelectedBoundaryOverlay();
    applySelectionOverlay(active?.mesh, []);
    applySelectionOverlay(active?.beamSolidMesh, []);
}

function paintCapacityEntries(
    mesh: THREE.Mesh,
    values: Array<{element_id: number; value: number | null; capacity_model_id?: string}>,
): boolean {
    const drawRanges = (mesh as unknown as {
        drawRanges?: Map<string, [number, number]>;
    }).drawRanges;
    if (!drawRanges) return false;

    const overlay = capacityColorOverlayFor(mesh);
    if (!overlay) return false;
    const colorAttr = overlay.geometry.getAttribute("color") as THREE.BufferAttribute | undefined;
    if (!colorAttr) return false;
    const colors = colorAttr.array as Float32Array;
    seedNeutralColors(colors);
    const tmp = new Float32Array(3);

    for (const entry of values) {
        if (entry.value == null || !isFinite(entry.value)) continue;
        const dr = drawRanges.get(`E${entry.element_id}`);
        if (!dr) continue;
        capacityUfColor(entry.value, tmp);
        const [vStart, vCount] = dr;
        for (let i = vStart; i < vStart + vCount; i++) {
            const off = i * 4;
            colors[off + 0] = tmp[0];
            colors[off + 1] = tmp[1];
            colors[off + 2] = tmp[2];
            colors[off + 3] = 1.0;
        }
    }

    colorAttr.needsUpdate = true;
    return true;
}

/** Show or hide the whole-model element-edge wireframe. Used by the capacity
 *  "Only definitions" view: with isolation on (and "show rest as wireframe"
 *  off) the wireframe of the non-capacity model would otherwise still draw
 *  over an otherwise-isolated scene. */
export function setFeaWireframeVisible(visible: boolean): void {
    if (active?.feaEdges) {
        active.feaEdges.visible = visible;
        requestRender();
    }
}

/** Element ids that belong to a capacity model in the active run — the set the
 *  "Only definitions" view keeps visible. */
function capacityKeepElementIds(): Set<number> | null {
    const store = useCapacityResultsStore.getState();
    if (!store.isolateDefinitions) return null;
    const results = store.results;
    const run = results?.runs.find((r) => r.id === store.activeRunId) ?? results?.runs[0];
    if (!run) return null;
    const keep = new Set<number>();
    for (const model of run.capacity_models) {
        for (const id of model.element_ids.all ?? []) keep.add(id);
    }
    return keep;
}

function capacityColorOverlayFor(mesh: THREE.Mesh): THREE.Mesh | null {
    if (!active) return null;
    const isBeam = active.beamSolidMesh === mesh;
    const keep = capacityKeepElementIds();
    const wantIsolated = keep !== null;
    const existing = isBeam ? active.beamSolidCapacityColorOverlay : active.capacityColorOverlay;
    // Rebuild when the isolation state changed: an isolated overlay collapses
    // non-capacity faces to zero area, a non-isolated one greys them. Comparing
    // here (rather than disposing from the isolation toggle) keeps it correct
    // regardless of React effect ordering.
    if (existing && existing.userData.capacityIsolated === wantIsolated) return existing;
    if (existing) disposeCapacityColorOverlay(isBeam ? "beam" : "main");

    const overlay = buildCapacityColorOverlay(mesh, keep);
    if (!overlay) return null;
    overlay.userData.capacityIsolated = wantIsolated;
    mesh.add(overlay);
    if (isBeam) active.beamSolidCapacityColorOverlay = overlay;
    else active.capacityColorOverlay = overlay;
    return overlay;
}

function buildCapacityColorOverlay(
    mesh: THREE.Mesh,
    keepElementIds: Set<number> | null,
): THREE.Mesh | null {
    const src = mesh.geometry;
    const indexAttr = src.getIndex();
    const posAttr = src.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!indexAttr || !posAttr || posAttr.itemSize !== 3) return null;

    const indexArr = indexAttr.array as Uint16Array | Uint32Array;
    const positions = new Float32Array(indexArr.length * 3);
    // RGBA vertex colours: alpha 0 (transparent) until a result value paints
    // the element, so un-valued elements keep the shaded base mesh look.
    const colors = new Float32Array(indexArr.length * 4);
    seedNeutralColors(colors);
    for (let i = 0; i < indexArr.length; i++) {
        const srcIdx = indexArr[i];
        const out = i * 3;
        positions[out + 0] = posAttr.getX(srcIdx);
        positions[out + 1] = posAttr.getY(srcIdx);
        positions[out + 2] = posAttr.getZ(srcIdx);
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geom.setAttribute("color", new THREE.BufferAttribute(colors, 4));
    duplicateMorphPositions(src, geom, indexArr);

    // "Only definitions": collapse every triangle that is not part of a capacity
    // model to zero area (all three verts coincident, base + morph) so the rest
    // of the model contributes no grey faces while the capacity panels stay lit.
    if (keepElementIds) {
        collapseNonCapacityTriangles(mesh, geom, indexArr, keepElementIds);
    }

    // Opaque, double-sided, unlit: the UF colour must read identically on both
    // faces of a shell. With <1 opacity the lit base mesh bleeds through and the
    // two sides look subtly different; a too-small polygon offset also lets the
    // coplanar base z-fight through on large models (large coordinates → coarse
    // depth precision), which shows as colour appearing on only one side. Keep
    // depthWrite off so the white capacity-boundary lines still layer on top.
    const mat = new THREE.MeshBasicMaterial({
        vertexColors: true,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 1.0,
        depthTest: true,
        depthWrite: false,
        polygonOffset: true,
        polygonOffsetFactor: -4,
        polygonOffsetUnits: -16,
    });
    const overlay = new THREE.Mesh(geom, mat);
    overlay.name = "capacity-color-overlay";
    overlay.renderOrder = 2;
    overlay.layers.mask = mesh.layers.mask;
    overlay.raycast = () => undefined;
    overlay.morphTargetInfluences = mesh.morphTargetInfluences;
    overlay.morphTargetDictionary = mesh.morphTargetDictionary;
    return overlay;
}

function duplicateMorphPositions(
    source: THREE.BufferGeometry,
    target: THREE.BufferGeometry,
    indexArr: Uint16Array | Uint32Array,
): void {
    const morphPositions = source.morphAttributes.position as THREE.BufferAttribute[] | undefined;
    if (!morphPositions?.length) return;
    target.morphAttributes.position = morphPositions.map((attr) => {
        const out = new Float32Array(indexArr.length * 3);
        for (let i = 0; i < indexArr.length; i++) {
            const srcIdx = indexArr[i];
            const dst = i * 3;
            out[dst + 0] = attr.getX(srcIdx);
            out[dst + 1] = attr.getY(srcIdx);
            out[dst + 2] = attr.getZ(srcIdx);
        }
        return new THREE.BufferAttribute(out, 3);
    });
    target.morphTargetsRelative = source.morphTargetsRelative === true;
}

/** Collapse every overlay triangle whose element is not in ``keepElementIds``
 *  to zero area, in both the base and morph position buffers, so the "Only
 *  definitions" view draws no grey faces for the rest of the model while the
 *  capacity panels keep their UF colours and deform in lockstep. */
function collapseNonCapacityTriangles(
    mesh: THREE.Mesh,
    geom: THREE.BufferGeometry,
    indexArr: Uint16Array | Uint32Array,
    keepElementIds: Set<number>,
): void {
    const drawRanges = (mesh as unknown as {
        drawRanges?: Map<string, [number, number]>;
    }).drawRanges;
    if (!drawRanges) return;

    // Mark overlay index positions that belong to a kept (capacity) element.
    const keepIdx = new Uint8Array(indexArr.length);
    for (const [key, [vStart, vCount]] of drawRanges) {
        const m = /^E(\d+)$/.exec(key);
        if (!m || !keepElementIds.has(Number(m[1]))) continue;
        for (let i = vStart; i < vStart + vCount && i < keepIdx.length; i++) keepIdx[i] = 1;
    }

    const posAttr = geom.getAttribute("position") as THREE.BufferAttribute;
    const morphs = (geom.morphAttributes.position ?? []) as THREE.BufferAttribute[];
    const collapse = (attr: THREE.BufferAttribute, k: number): void => {
        const x = attr.getX(k);
        const y = attr.getY(k);
        const z = attr.getZ(k);
        attr.setXYZ(k + 1, x, y, z);
        attr.setXYZ(k + 2, x, y, z);
    };
    // Draw ranges are triangle-aligned, so the first vertex of each triangle
    // tells us whether the whole triangle is kept.
    for (let k = 0; k + 2 < indexArr.length; k += 3) {
        if (keepIdx[k]) continue;
        collapse(posAttr, k);
        for (const morph of morphs) collapse(morph, k);
    }
    posAttr.needsUpdate = true;
    for (const morph of morphs) morph.needsUpdate = true;
}

function updateCapacitySelectionOverlay(
    run: {
        capacity_models: Array<{
            id: string;
            panel_group: string;
            element_ids: {all?: number[]};
            stiffeners?: Array<Record<string, unknown>>;
        }>;
    },
    selectedModelId: string | null,
): void {
    disposeCapacitySelectedBoundaryOverlay();
    const rangeIds = selectedModelId
        ? run.capacity_models
            .find((model) => model.id === selectedModelId)
            ?.element_ids.all
            ?.map((id) => `E${id}`) ?? []
        : [];

    // Keep capacity selection visually edge-based. The default mesh face
    // selection color is too close to result colors and can obscure UF plots.
    applySelectionOverlay(active?.mesh, []);
    applySelectionOverlay(active?.beamSolidMesh, []);

    if (!selectedModelId || rangeIds.length === 0) return;
    const selectedModel = run.capacity_models.find((model) => model.id === selectedModelId);
    if (!selectedModel) return;
    const overlay = buildCapacityBoundaryOverlay(
        [selectedModel],
        0xff1f3d,
        "capacity-selected-model-boundary",
        false,
    );
    if (overlay && active?.mesh) {
        active.mesh.add(overlay);
        active.capacitySelectedBoundaryOverlay = overlay;
        linkLineMorphToMesh(active.mesh);
    }

    // When an individual stiffener row is selected within a multi-stiffener
    // panel, outline that stiffener's own strip (line + tributary plate) in
    // amber so the specific capacity model under inspection stands out.
    const selectedResultId = useCapacityResultsStore.getState().selectedResultId;
    const stiffeners = selectedModel.stiffeners ?? [];
    const stiffName = selectedResultId?.split("::").pop() ?? null;
    if (!stiffName || stiffeners.length < 2) return;
    const stiff = stiffeners.find((s) => String(s.name) === stiffName);
    if (!stiff) return;
    const stripIds = [
        ...(((stiff.element_ids as number[] | undefined) ?? [])),
        ...(((stiff.tributary_plate_ids as number[] | undefined) ?? [])),
    ];
    if (stripIds.length === 0) return;
    const strip = buildCapacityBoundaryOverlay(
        [
            {
                id: `${selectedModel.id}::${stiffName}`,
                panel_group: selectedModel.panel_group,
                element_ids: {all: stripIds},
            },
        ],
        0xffc53d,
        "capacity-selected-strip-boundary",
        false,
    );
    if (strip && active?.mesh) {
        active.mesh.add(strip);
        active.capacitySelectedStripOverlay = strip;
        linkLineMorphToMesh(active.mesh);
    }
}

function applySelectionOverlay(mesh: THREE.Mesh | undefined, rangeIds: string[]): void {
    const maybeSelectable = mesh as unknown as {
        updateSelectionGroups?: (rangeIds: string[]) => void;
    } | undefined;
    maybeSelectable?.updateSelectionGroups?.(rangeIds);
}

// Failed-model definition edges (a check raised for this model/case).
const CAPACITY_FAILED_EDGE_COLOR = 0xef4444;

function rebuildCapacityBoundaryOverlay(
    models: Array<{id: string; panel_group: string; element_ids: {all?: number[]}}>,
    failedModelIds?: Set<string>,
): void {
    disposeCapacityBoundaryOverlay();
    const overlay = buildCapacityBoundaryOverlay(
        models,
        0xf8fafc,
        "capacity-model-boundaries",
        true,
        failedModelIds,
    );
    if (overlay && active?.mesh) {
        active.mesh.add(overlay);
        active.capacityBoundaryOverlay = overlay;
        linkLineMorphToMesh(active.mesh);
    }
}

function buildCapacityBoundaryOverlay(
    models: Array<{id: string; panel_group: string; element_ids: {all?: number[]}}>,
    color: number,
    name: string,
    depthTest: boolean,
    failedModelIds?: Set<string>,
): THREE.LineSegments | null {
    if (!active?.mesh) return null;
    const mesh = active.mesh;
    const geometry = mesh.geometry;
    const indexAttr = geometry.getIndex();
    const drawRanges = (mesh as unknown as {
        drawRanges?: Map<string, [number, number]>;
    }).drawRanges;
    if (!indexAttr || !drawRanges) return null;
    const indexArr = indexAttr.array as Uint16Array | Uint32Array;
    const edgeIndices: number[] = [];
    // Vertices that belong to a failed model's boundary — painted red.
    const failedVerts = new Set<number>();

    for (const model of models) {
        const isFailed = failedModelIds?.has(model.id) ?? false;
        const edgeCounts = new Map<string, [number, number, number]>();
        for (const elementId of model.element_ids.all ?? []) {
            const dr = drawRanges.get(`E${elementId}`);
            if (!dr) continue;
            const [vStart, vCount] = dr;
            for (let i = vStart; i + 2 < vStart + vCount; i += 3) {
                addBoundaryEdge(edgeCounts, indexArr[i], indexArr[i + 1]);
                addBoundaryEdge(edgeCounts, indexArr[i + 1], indexArr[i + 2]);
                addBoundaryEdge(edgeCounts, indexArr[i + 2], indexArr[i]);
            }
        }
        for (const [, [a, b, count]] of edgeCounts) {
            if (count !== 1) continue;
            edgeIndices.push(a, b);
            if (isFailed) {
                failedVerts.add(a);
                failedVerts.add(b);
            }
        }
    }
    if (edgeIndices.length === 0) return null;

    const lineGeom = new THREE.BufferGeometry();
    lineGeom.setAttribute("position", geometry.attributes.position);
    lineGeom.setIndex(new THREE.BufferAttribute(new Uint32Array(edgeIndices), 1));
    const matOpts: THREE.LineBasicMaterialParameters = {
        color,
        depthTest,
        depthWrite: false,
        transparent: true,
        opacity: depthTest ? 0.92 : 1.0,
    };
    if (failedVerts.size > 0) {
        // Per-vertex colours so failed models read red while the rest stay the
        // normal boundary colour, all within one morph-linked LineSegments.
        const posCount = (geometry.attributes.position as THREE.BufferAttribute).count;
        const colors = new Float32Array(posCount * 3);
        const base = new THREE.Color(color);
        for (let i = 0; i < posCount; i++) {
            colors[i * 3 + 0] = base.r;
            colors[i * 3 + 1] = base.g;
            colors[i * 3 + 2] = base.b;
        }
        const red = new THREE.Color(CAPACITY_FAILED_EDGE_COLOR);
        for (const v of failedVerts) {
            colors[v * 3 + 0] = red.r;
            colors[v * 3 + 1] = red.g;
            colors[v * 3 + 2] = red.b;
        }
        lineGeom.setAttribute("color", new THREE.BufferAttribute(colors, 3));
        matOpts.vertexColors = true;
        matOpts.color = 0xffffff; // let the per-vertex colour show through
    }
    const lineMat = new THREE.LineBasicMaterial(matOpts);
    const overlay = new THREE.LineSegments(lineGeom, lineMat);
    overlay.name = name;
    overlay.layers.mask = mesh.layers.mask;
    overlay.renderOrder = depthTest ? 3 : 6;
    return overlay;
}

function disposeCapacityBoundaryOverlay(): void {
    if (!active?.capacityBoundaryOverlay) return;
    active.capacityBoundaryOverlay.removeFromParent();
    active.capacityBoundaryOverlay.geometry.dispose();
    const material = active.capacityBoundaryOverlay.material;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material.dispose();
    active.capacityBoundaryOverlay = undefined;
}

function disposeCapacitySelectedBoundaryOverlay(): void {
    for (const key of ["capacitySelectedBoundaryOverlay", "capacitySelectedStripOverlay"] as const) {
        const overlay = active?.[key];
        if (!overlay) continue;
        overlay.removeFromParent();
        overlay.geometry.dispose();
        const material = overlay.material;
        if (Array.isArray(material)) material.forEach((m) => m.dispose());
        else material.dispose();
        active![key] = undefined;
    }
}

function disposeCapacityColorOverlay(which: "main" | "beam"): void {
    if (!active) return;
    const overlay = which === "main" ? active.capacityColorOverlay : active.beamSolidCapacityColorOverlay;
    if (!overlay) return;
    overlay.removeFromParent();
    overlay.geometry.dispose();
    const material = overlay.material;
    if (Array.isArray(material)) material.forEach((m) => m.dispose());
    else material.dispose();
    if (which === "main") active.capacityColorOverlay = undefined;
    else active.beamSolidCapacityColorOverlay = undefined;
}

function addBoundaryEdge(
    edgeCounts: Map<string, [number, number, number]>,
    a: number,
    b: number,
): void {
    const lo = Math.min(a, b);
    const hi = Math.max(a, b);
    const key = `${lo}:${hi}`;
    const prev = edgeCounts.get(key);
    if (prev) prev[2] += 1;
    else edgeCounts.set(key, [lo, hi, 1]);
}

/** Seed the RGBA capacity colour buffer fully transparent: elements without a
 *  result value contribute nothing, so the normally-shaded base mesh shows
 *  through instead of a flat unlit grey. */
function seedNeutralColors(colors: Float32Array): void {
    colors.fill(0);
}

// Genie "UfTot" discrete colour bands (no gradient between them) — sampled from
// .local/reference/genie_uf_color_scheme/genie_uf_color_scheme.png. Thresholds at
// 0.2 / 0.4 / 0.6 / 0.8 / 1.0. RGB in 0..1.
const CAPACITY_UF_BANDS: ReadonlyArray<readonly [number, readonly [number, number, number]]> = [
    [1.0, [1.0, 0.0, 0.0]], // >= 1.0  #FF0000 red
    [0.8, [1.0, 0.6431, 0.0]], // >= 0.8  #FFA400 orange
    [0.6, [1.0, 1.0, 0.0]], // >= 0.6  #FFFF00 yellow
    [0.4, [0.0, 0.498, 0.0]], // >= 0.4  #007F00 green
    [0.2, [0.0, 1.0, 1.0]], // >= 0.2  #00FFFF cyan
    [0.0, [0.0, 0.7451, 1.0]], // <  0.2  #00BEFF light blue
];

function capacityUfColor(value: number, out: Float32Array): void {
    const band = CAPACITY_UF_BANDS.find(([threshold]) => value >= threshold);
    const [r, g, b] = (band ?? CAPACITY_UF_BANDS[CAPACITY_UF_BANDS.length - 1])[1];
    out[0] = r;
    out[1] = g;
    out[2] = b;
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
            await replace_model(url, async (gltf_scene) => {
                feaRoot = gltf_scene;
                if (afemEntries.length > 0) {
                    installAfemUserData(gltf_scene, afemEntries);
                }
            }, undefined, /* translate */ true);
            const ms = useModelState.getState();
            ms.setModelUrl(url, SceneOperations.REPLACE);
            ms.setLoadedSourceName(sourceName);
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

        active = {
            sourceName,
            manifest,
            mesh,
            basePositions,
            capacityFetch: {fetcher, rangeFetcher, cacheKey},
        };
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

            // AFEG over the beam-solid mesh: element-boundary edges
            // (perimeter + axial seams between adjacent beam-elements).
            // Without these the solid beams render as one continuous
            // tube; with them the user can see where one beam ends and
            // the next starts. Same wiring pattern as the main mesh's
            // wireframe — share position + morph attributes so the
            // wireframe deforms in lockstep. Gated by the same
            // `hideElementEdges` perf toggle as the main wireframe.
            if (
                manifest.mesh.beam_solids_edges_url
                && !usePerfStore.getState().hideElementEdges
            ) {
                try {
                    const beamEdgeIndices = await fetchMeshEdges(
                        fetcher,
                        manifest.mesh.beam_solids_edges_url,
                    );
                    if (beamEdgeIndices.length > 0) {
                        const lineGeom = new THREE.BufferGeometry();
                        lineGeom.setAttribute(
                            "position",
                            beamSolid.mesh.geometry.attributes.position,
                        );
                        lineGeom.setIndex(
                            new THREE.BufferAttribute(beamEdgeIndices, 1),
                        );
                        const lineMat = new THREE.LineBasicMaterial({
                            color: 0x111111,
                            depthTest: true,
                        });
                        const segments = new THREE.LineSegments(lineGeom, lineMat);
                        segments.name = "fea-beam-solid-element-edges";
                        // Layer 1: rendered but not pickable — beam-solid
                        // face picking goes through the parent
                        // CustomBatchedMesh; the wireframe is decorative.
                        segments.layers.set(1);
                        // Inherit beam-solid visibility so toggling the
                        // solid mesh on/off hides its wireframe too.
                        segments.visible = beamSolid.mesh.visible;
                        // Morph attribute + influences are linked after
                        // installBeamSolidWarp seeds them — see
                        // applyFieldToBeamSolids further below.
                        beamSolid.mesh.add(segments);
                        active.beamSolidEdges = segments;
                    }
                } catch (err) {
                    // eslint-disable-next-line no-console
                    console.warn(
                        "[fea-streaming] failed to load beam-solid edges:",
                        err,
                    );
                }
            }

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
                if (edgeIndices.length > 0) {
                    const lineGeom = new THREE.BufferGeometry();
                    lineGeom.setAttribute("position", mesh.geometry.attributes.position);
                    lineGeom.setIndex(new THREE.BufferAttribute(edgeIndices, 1));
                    const lineMat = new THREE.LineBasicMaterial({
                        color: 0x111111,
                        depthTest: true,
                    });
                    const segments = new THREE.LineSegments(lineGeom, lineMat);
                    segments.name = "fea-element-edges";
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
                    if (active) active.feaEdges = segments;
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

    // Load capacity results after any model replacement. replace_model()
    // clears the active FEA session, including the capacity store; loading
    // the sidecar before that would make the Capacity panel disappear even
    // though the sidecar request succeeded.
    await loadCapacityResultsIfPresent(fetcher, scope, sourceName, manifest);

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

        // Beam-solid mesh: nodal fields don't have a sensible
        // per-vertex value here (the beam-solid vertices aren't FEA
        // nodes). Turn vertexColors off so any stale element-field
        // colouring stops contributing and the GLB's base material
        // shows. Cheap toggle — no buffer rewrite needed.
        //
        // Warp is independent of colour: install the lerped nodal
        // warp on the beam-solid mesh so a displacement field flexes
        // the solid beams in lockstep with the rest of the structure.
        // Without this, scaling the morph influence ×100 would leave
        // rigid solid beams sitting at undeformed positions while the
        // shells fly off.
        if (active.beamSolidMesh) {
            const disableVc = (mat: THREE.Material) => {
                if ("vertexColors" in mat) {
                    (mat as unknown as {vertexColors: boolean}).vertexColors = false;
                    mat.needsUpdate = true;
                }
            };
            const m = active.beamSolidMesh.material;
            if (Array.isArray(m)) m.forEach(disableVc);
            else if (m) disableVc(m as THREE.Material);

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
        animStore.setFieldName(fieldName);
        if (reduction != null) animStore.setReduction(reduction);
        animStore.setColormap(colormap);
    } else {
        // Field-less FEM mesh (model only): no results -> NO simulation session, so
        // SimulationControls + the results-only "show in data" action stay hidden. The
        // beam-solids toggle acts on the module-level `active` mesh, not the session, so it
        // still works from the Scene > FEM panel.
        animStore.setSessionActive(false);
        animStore.setFieldName(null);
        animStore.setNSteps(1);
        animStore.setStepIndex(0);
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
            manifest.fields[0];
        const reduction = field.default_view?.reduction ?? "magnitude";
        await load_fea_streaming({
            sourceName,
            manifest,
            fieldName: field.name_canonical,
            stepIndex: 0,
            reduction,
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
