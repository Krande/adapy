import * as THREE from "three";

import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import {runtime} from "@/runtime/config";
import {fetchFieldBlob} from "@/services/feaFieldBlob";
import {fetchMeshEdges} from "@/services/feaMeshEdges";
import {fetchMeshElements, MeshElementEntry} from "@/services/feaMeshElements";
import {FeaManifest, FeaManifestField, viewerApi} from "@/services/viewerApi";
import {sceneRef} from "@/state/refs";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useConversionStore} from "@/state/conversionStore";
import {applyFieldToMesh} from "../fea/applyField";
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
}

let active: ActiveFeaStreaming | null = null;

/** Drop the cached state on next call (e.g. when the user replaces
 * the scene with a different file). The blob cache lives separately
 * in feaFieldBlob.ts. Also resets the deformation-animation store
 * so the SimulationControls UI doesn't keep showing FEA-mode
 * controls for a mesh that's no longer in the scene, and hides the
 * controls panel entirely when no GLTF clips are around to show in
 * the fallback path. */
export function clearActiveFeaStreaming(): void {
    active = null;
    useFeaAnimationStore.getState().reset();
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
    scope: string,
    sourceName: string,
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
        const parsed = await fetchFieldBlob(scope, sourceName, colorField);
        return {field: colorField, stepValues: parsed.steps[stepIndex]};
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
    const parsed = await fetchFieldBlob(scope, sourceName, dispField);
    return {field: dispField, stepValues: parsed.steps[warpStep]};
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
    fieldName: string;
    stepIndex: number;
    reduction: string;
    displacementScale?: number;
    /** Colormap ID — one of the keys in ``COLORMAPS``. Optional so
     * existing call-sites that don't care still work; we fall back to
     * the active store value (and from there to viridis if unset). */
    colormap?: string;
}): Promise<void> {
    if (!runtime.isRestMode()) {
        throw new Error("FEA streaming viewer is only available in REST mode");
    }
    const {sourceName, manifest, fieldName, stepIndex, reduction} = args;
    const displacementScale = args.displacementScale ?? 1;
    const colormap =
        args.colormap ?? useFeaAnimationStore.getState().colormap;

    if (!manifest || !Array.isArray(manifest.fields)) {
        throw new Error(
            "load_fea_streaming: manifest is missing or has no fields array",
        );
    }
    const field = manifest.fields.find((f) => f.name_canonical === fieldName);
    if (!field) {
        throw new Error(`field ${fieldName!} not found in manifest`);
    }
    if (stepIndex < 0 || stepIndex >= field.n_steps) {
        throw new Error(
            `step index ${stepIndex} out of range (0..${field.n_steps - 1})`,
        );
    }

    const scope = scopeUrlPart(useScopeStore.getState().current);

    // (Re-)load the mesh into the scene if we don't already have it
    // for this source. Switching field-within-source keeps the same
    // mesh; switching source forces a reload.
    if (!active || active.sourceName !== sourceName) {
        const meshKey = `_derived/${sourceName.replace(/^\/+/, "")}.fea/${manifest.mesh.url}`;
        const buf = await viewerApi.getBlob(scope, meshKey);
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
                    scope,
                    sourceName,
                    manifest.mesh.elements_url,
                );
            } catch (err) {
                // Selection wiring is best-effort: the picker still
                // renders without it, just at whole-mesh granularity.
                // eslint-disable-next-line no-console
                console.warn("[fea-streaming] failed to load mesh elements:", err);
            }
        }

        try {
            await replace_model(url, async (gltf_scene) => {
                if (afemEntries.length > 0) {
                    installAfemUserData(gltf_scene, afemEntries);
                }
            });
            const ms = useModelState.getState();
            ms.setModelUrl(url, SceneOperations.REPLACE);
            ms.setLoadedSourceName(sourceName);
        } catch (err) {
            URL.revokeObjectURL(url);
            throw err;
        }

        const scene = sceneRef.current;
        if (!scene) throw new Error("scene not ready");
        const mesh = findFirstMesh(scene);
        if (!mesh) throw new Error("loaded GLB has no mesh");
        const basePositions = snapshotBasePositions(mesh.geometry);

        active = {sourceName, manifest, mesh, basePositions};
        // Material flags (vertexColors + morphTargets) are flipped on
        // inside applyFieldToMesh so they cover both the array-typed
        // material that prepareLoadedModel installs on
        // CustomBatchedMesh and the plain-material fallback.

        // Element-edge wireframe overlay. The bake emits an explicit
        // edge sidecar (deduped uint32 pairs from each cell's
        // ElemShape.edges) so the wireframe shows real element
        // boundaries — not the diagonals from quad-face triangulation.
        // Sharing the mesh's position attribute + morph attribute +
        // influences array means deformation drives both face and
        // line rendering from a single buffer / single uniform.
        if (manifest.mesh.edges_url) {
            try {
                const edgeIndices = await fetchMeshEdges(
                    scope,
                    sourceName,
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
                }
            } catch (err) {
                // Wireframe overlay is decorative — log and continue
                // so a missing/corrupt sidecar doesn't block rendering.
                // eslint-disable-next-line no-console
                console.warn("[fea-streaming] failed to load mesh edges:", err);
            }
        }
    }

    const parsed = await fetchFieldBlob(scope, sourceName, field);
    const colorStepValues = parsed.steps[stepIndex];

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
    const warpEnabled = useFeaAnimationStore.getState().warpEnabled;
    const warpInfo = await resolveWarpSource(
        scope,
        sourceName,
        manifest,
        field,
        stepIndex,
        warpEnabled,
    );

    applyFieldToMesh({
        mesh: active.mesh,
        basePositions: active.basePositions,
        colorField: field,
        colorStepValues,
        reduction,
        warpField: warpInfo?.field,
        warpStepValues: warpInfo?.stepValues,
        displacementScale,
        colormap,
    });

    // Link the edge overlay's morph state to the mesh's so the
    // wireframe tracks deformation. Idempotent: re-running just
    // re-links, which is fine — the references are stable across
    // step changes.
    linkLineMorphToMesh(active.mesh);

    // Register the session with the animation store so
    // SimulationControls renders the deformation-scale slider /
    // play / stop instead of the GLTF-clip controls. Range follows
    // the field's analysis_kind: static = [0, 1] (one-directional),
    // eigen = [-1, +1] (mode shape has no inherent sign).
    const animStore = useFeaAnimationStore.getState();
    const range: [number, number] =
        field.analysis_kind === "eigen" ? [-1, 1] : [0, 1];
    animStore.setSessionActive(true);
    animStore.setMesh(active.mesh);
    animStore.setRange(range);
    animStore.setFactor(displacementScale);
    animStore.setStepIndex(stepIndex);
    animStore.setNSteps(field.n_steps);
    animStore.setSourceName(sourceName);
    animStore.setManifest(manifest);
    animStore.setFieldName(fieldName);
    animStore.setReduction(reduction);
    animStore.setColormap(colormap);

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
    animStore.setApplyStep(async (newStepIndex: number) => {
        await load_fea_streaming({
            sourceName,
            manifest,
            fieldName,
            stepIndex: newStepIndex,
            reduction,
        });
    });

    // Auto-show the SimulationControls panel on first apply so the
    // user doesn't need to find a hidden toggle for a deformation
    // session they just kicked off. Idempotent — re-applying with a
    // panel already open is a no-op.
    const generalAnimStore = useAnimationStore.getState();
    if (!generalAnimStore.isControlsVisible) {
        generalAnimStore.setIsControlsVisible(true);
    }
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
        sourceKey: sourceName,
        jobId: "",
        derivedKey: "",
        status: "queued",
        progress: 0,
        stage: "queuing fea bake",
        error: null,
        startedAt,
    });

    let manifest: FeaManifest;
    try {
        manifest = await viewerApi.feaManifest(scope, sourceName, {
            onProgress: ({jobId, stage, progress, status}) => {
                convStore.setJob(storeKey, {
                    sourceKey: sourceName,
                    jobId,
                    derivedKey: "",
                    status,
                    progress,
                    stage,
                    error: null,
                    startedAt,
                });
            },
        });
        // Mark done so the toast self-removes (ConversionProgress
        // filters out done jobs). Manifest fetch succeeded; the
        // render path below shouldn't keep the toast on screen.
        convStore.setJob(storeKey, {
            sourceKey: sourceName,
            jobId: "",
            derivedKey: "",
            status: "done",
            progress: 1,
            stage: "ready",
            error: null,
            startedAt,
        });
    } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        convStore.setJob(storeKey, {
            sourceKey: sourceName,
            jobId: "",
            derivedKey: "",
            status: "error",
            progress: 0,
            stage: "failed",
            error: msg,
            startedAt,
        });
        throw err;
    }
    if (!manifest || !Array.isArray(manifest.fields) || manifest.fields.length === 0) {
        // Bake produced a manifest but no fields — usually means the
        // source has only non-nodal data and nodal_only=true filtered
        // it all out. Surface as a console warning rather than a
        // throw so the toggle doesn't get stuck in an error state.
        // eslint-disable-next-line no-console
        console.warn(
            `[fea-streaming] manifest for ${sourceName} has no nodal fields; nothing to load`,
        );
        return;
    }
    // Pick the first *nodal* field as the default. Element fields
    // (per_type populated, no top-level blob) need the AFEL render
    // path that doesn't exist yet; landing on one would crash
    // ``fetchFieldBlob`` because ``field.blob`` is undefined.
    // Prefer ``category === "displacement"`` so a fresh load opens
    // on the deformation field that the warp source wants anyway.
    const renderable = manifest.fields.filter((f) => f.blob);
    if (renderable.length === 0) {
        // eslint-disable-next-line no-console
        console.warn(
            `[fea-streaming] manifest for ${sourceName} has only element ` +
            `fields; the viewer hasn't wired up element-field rendering yet`,
        );
        return;
    }
    const field =
        renderable.find((f) => f.category === "displacement") ?? renderable[0];
    const reduction = field.default_view?.reduction ?? "magnitude";
    await load_fea_streaming({
        sourceName,
        manifest,
        fieldName: field.name_canonical,
        stepIndex: 0,
        reduction,
        displacementScale: 1,
    });
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
