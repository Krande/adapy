import * as THREE from "three";

import {SceneOperations} from "@/flatbuffers/scene/scene-operations";

import {fetchElemFieldStep} from "@/services/feaElemFieldBlob";
import {fetchFieldStep, makeViewerApiFetcher} from "@/services/feaFieldBlob";
import {fetchMeshElements, MeshElementEntry} from "@/services/feaMeshElements";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {capabilities} from "@/services/capabilities";
import {runResultSidecarLoaders} from "@/plugins/sidecarLoaders";
import type {SidecarFetcher} from "@/plugins/registry";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {useModelState} from "@/state/modelState";
import {useModelSessionStore} from "@/state/modelSession";
import {useAnimationStore} from "@/state/animationStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useColorStore} from "@/state/colorLegendStore";
import {useConversionStore} from "@/state/conversionStore";
import {applyFieldToMesh} from "../fea/applyField";
import {applyElemFieldToMesh} from "../fea/applyElemField";
import {resolveContourRange} from "../fea/contourScale";
import {selectedResultRange} from "../fea/resultUnits";
import {autoWarpScale} from "../fea/warpScale";
import {
    noteFieldSourceLoaded,
    requestingSceneColorOwner,
} from "../fea/modeSceneColor";
import {beamSolidNodalColors} from "../fea/beamSolidNodalColors";
import {expandSourceTriples, sourceVertexIndices} from "../fea/elementLocalGeometry";
import {replace_model} from "./update_scene_from_message";
import {feaSession as session} from "../fea/streaming/session";
import {findFirstMesh, installAfemUserData, snapshotBasePositions} from "../fea/streaming/sceneMesh";
import {findDisplacementField, installBeamSolidWarp, linkLineMorphToMesh, resolveWarpSource} from "../fea/streaming/warp";
import {fetchBeamSolidWarpSidecar, tryLoadBeamSolids} from "../fea/streaming/beamSolids";
import {installElementEdges} from "../fea/streaming/elementEdges";
import {refreshUndeformedGhost, setFeaResultColorsVisible, syncFeaOverlayVisibility} from "../fea/streaming/visibility";

export {
    getActiveFeaMesh,
    getActiveFeaSelectedRangeIds,
    hasBeamSolids,
    setActiveFeaSelectedRangeIds,
} from "../fea/streaming/session";
export {clearActiveFeaStreaming} from "../fea/streaming/teardown";
export {
    feaElementEdgesVisible,
    hasFeaElementEdges,
    refreshUndeformedGhost,
    setBeamSolidsVisible,
    setFeaElementEdgesVisible,
    setFeaResultColorsVisible,
    setFeaUndeformedGhost,
    syncFeaOverlayVisibility,
} from "../fea/streaming/visibility";

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
    /**
     * The sweep slider's own position, when the caller is starting a session and
     * wants the slider moved to it.
     *
     * Distinct from ``displacementScale`` on purpose. That one is the MORPH
     * INFLUENCE — the slider position multiplied by the warp-scale knob — and
     * writing it back into the slider was how selecting a component moved the
     * indicator without moving the model: at a warp scale of 0.2 a slider on 0.55
     * sends an influence of 0.11, the slider then read 0.11, and the shape did not
     * change because the influence had not. Compounding, too: the next selection
     * would have sent 0.022.
     *
     * Omit it and the slider is left where the user put it, which is what every
     * re-apply wants — component, step, layer, colormap. Only a fresh load passes
     * it.
     */
    sliderFactor?: number;
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
    // The mesh, field and sidecar blobs are read by key from the bake's
    // _derived/ namespace, which only a transport that bakes and serves FEA
    // manifests can do -- what "REST mode" used to stand in for.
    if (!capabilities.fea.supports("fetchManifest")) {
        throw new Error("FEA streaming viewer is only available in REST mode");
    }
    const {sourceName, manifest, fieldName, stepIndex, reduction, onStage, signal} = args;
    // Who this load paints for, taken NOW rather than when it lands: an owning
    // mode entered while the fetch is in flight must not be credited with a
    // field the user picked before it, and a mode's own repaint stays its own
    // if the user leaves before it lands.
    const colorOwner = requestingSceneColorOwner(sourceName);
    const displacementScale = args.displacementScale ?? 1;
    const {sliderFactor} = args;
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
    if (!session.active || session.active.sourceName !== sourceName) {
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
            const feaGroup = await replace_model({
                url,
                prepareHook: async (gltf_scene) => {
                    feaRoot = gltf_scene;
                    if (afemEntries.length > 0) {
                        installAfemUserData(gltf_scene, afemEntries);
                    }
                },
                translate: true,
            });
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
                const sceneRoot = getViewerRuntime().scene.current;
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
            // FemConceptsController's adaExtension parse finds nothing
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

        const scene = getViewerRuntime().scene.current;
        if (!scene) throw new Error("scene not ready");
        // Scope to the loaded GLB root, not the whole scene — a
        // fem_concepts glyph or other overlay mesh would otherwise be
        // picked up as active.mesh and crash applyFieldToMesh.
        const mesh = findFirstMesh(feaRoot ?? scene);
        if (!mesh) throw new Error("loaded GLB has no mesh");
        const basePositions = snapshotBasePositions(mesh.geometry);

        session.active = {sourceName, manifest, mesh, basePositions};
        // Name the session for what it is. The handle is how this module finds
        // its mesh again; `kind` is how anything else can tell a streaming FEA
        // result from a CAD load without sniffing the file extension.
        {
            const open = useModelSessionStore.getState().ensure();
            open.identity.kind = "fea";
            open.identity.sourceName ??= sourceName;
            open.identity.url = url;
        }
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
            session.active.beamSolidMesh = beamSolid.mesh;
            session.active.beamSolidBasePositions = beamSolid.basePositions;

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

            const warp = await fetchBeamSolidWarpSidecar(fetcher, manifest, beamSolid.basePositions);
            if (warp) session.active.beamSolidWarp = warp;
        }

        // Element-edge wireframe overlays, from the bake's edge sidecar. The
        // index is kept on the session so the undeformed reference wireframe can
        // be rebuilt without re-fetching.
        const edgeIndices = await installElementEdges(mesh, fetcher, manifest);
        if (edgeIndices && session.active) session.active.edgeIndices = edgeIndices;
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
    // Read once, applied to every surface that carries the field. Splitting the
    // scale between the shells, the beam solids and the beam lines is how a model
    // comes to show three different answers to the same question.
    const contour = useFeaAnimationStore.getState().contour;
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
            mesh: session.active.mesh,
            basePositions: session.active.basePositions,
            colorField: field,
            perTypeStepValues,
            layer,
            ipReduction,
            reduction: reductionStr,
            warpField: warpInfo?.field,
            warpStepValues: warpInfo?.stepValues,
            displacementScale,
            colormap,
            contour,
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
        //
        // The SAME influence as the main mesh, passed explicitly. After the
        // first apply the beam-solid mesh shares the main mesh's
        // ``morphTargetInfluences`` array (installBeamSolidWarp links them), so
        // the influence this call writes lands on the main mesh too. Left to
        // its default of 1 it reset the whole model to an unscaled warp on
        // every element-field repaint -- which is what the warp toggle, a
        // component change or a colormap change all are. With an auto-derived
        // scale of 50 on a deck deforming by millimetres, a warp at 1 cannot be
        // told from no warp at all, and the toggle looked dead.
        if (session.active.beamSolidMesh && session.active.beamSolidBasePositions) {
            applyElemFieldToMesh({
                mesh: session.active.beamSolidMesh,
                basePositions: session.active.beamSolidBasePositions,
                colorField: field,
                perTypeStepValues,
                layer,
                ipReduction,
                reduction: reductionStr,
                displacementScale,
                colormap,
                contour,
                nodalAverage: false,
            });
            if (session.active.beamSolidWarp) {
                installBeamSolidWarp(
                    session.active.mesh,
                    session.active.beamSolidMesh,
                    session.active.beamSolidBasePositions,
                    session.active.beamSolidWarp,
                    warpInfo?.field,
                    warpInfo?.stepValues,
                );
            }
        }
    } else {
        const colorStepValues = await fetchFieldStep(rangeFetcher, fetcher, field, stepIndex, cacheKey);

        applyFieldToMesh({
            mesh: session.active.mesh,
            basePositions: session.active.basePositions,
            colorField: field,
            colorStepValues,
            reduction: reductionStr,
            warpField: warpInfo?.field,
            warpStepValues: warpInfo?.stepValues,
            displacementScale,
            colormap,
            contour,
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
        if (session.active.beamSolidMesh) {
            const setVc = (mat: THREE.Material, on: boolean) => {
                if ("vertexColors" in mat && (mat as unknown as {vertexColors: boolean}).vertexColors !== on) {
                    (mat as unknown as {vertexColors: boolean}).vertexColors = on;
                    mat.needsUpdate = true;
                }
            };
            let painted = false;
            if (session.active.beamSolidWarp && session.active.beamSolidBasePositions) {
                const sourceColors = beamSolidNodalColors(
                    field,
                    colorStepValues,
                    reductionStr,
                    session.active.beamSolidWarp,
                    colormap,
                    session.active.basePositions.length / 3,
                    contour,
                );
                if (sourceColors) {
                    const geom = session.active.beamSolidMesh.geometry;
                    // Through the element-local expansion, if one is cached on this
                    // geometry from an earlier element field. Same reason the morph
                    // goes through it: a buffer sized for the original vertex count
                    // does not fit an expanded geometry.
                    const nSource = session.active.beamSolidWarp.n_verts;
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
            const m = session.active.beamSolidMesh.material;
            if (Array.isArray(m)) m.forEach((mat) => setVc(mat, painted));
            else if (m) setVc(m as THREE.Material, painted);

            if (session.active.beamSolidWarp && session.active.beamSolidBasePositions) {
                installBeamSolidWarp(
                    session.active.mesh,
                    session.active.beamSolidMesh,
                    session.active.beamSolidBasePositions,
                    session.active.beamSolidWarp,
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
    linkLineMorphToMesh(session.active.mesh);
    // Same link for the beam-solid mesh's element-edge wireframe so
    // the seams between adjacent beam elements stay attached to the
    // deformed solid mesh under any morph scale.
    if (session.active.beamSolidMesh) {
        linkLineMorphToMesh(session.active.beamSolidMesh);
    }

    // Re-apply the undeformed-wireframe preference. It survives loads and step
    // changes, and the ghost has to be rebuilt after one: the base positions it
    // copies belong to the source that was just loaded.
    refreshUndeformedGhost();

    // Re-apply the view preferences a load resets: which beam rendering shows, and
    // whether element edges are drawn. Both outlive the mesh they were set on.
    {
        const s = useFeaAnimationStore.getState();
        setFeaResultColorsVisible(s.resultColorsVisible);
        syncFeaOverlayVisibility();
    }

    // Register the session with the animation store so
    // SimulationControls renders the deformation-scale slider /
    // play / stop instead of the GLTF-clip controls. Range follows
    // the field's analysis_kind: static = [0, 1] (one-directional),
    // eigen = [-1, +1] (mode shape has no inherent sign).
    const animStore = useFeaAnimationStore.getState();
    animStore.setMesh(session.active.mesh);
    animStore.setSourceName(sourceName);
    animStore.setManifest(manifest);
    if (field) {
        // Results present -> activate the FEA session (SimulationControls: step slider / field
        // selector / warp). Range follows analysis_kind: static = [0, 1], eigen = [-1, +1].
        animStore.setSessionActive(true);
        const range: [number, number] = field.analysis_kind === "eigen" ? [-1, 1] : [0, 1];
        animStore.setRange(range);
        // Only when the caller asked. See ``sliderFactor`` on the argument type:
        // the influence and the slider are different numbers, and equating them
        // moved the indicator on every component change.
        if (sliderFactor !== undefined) animStore.setFactor(sliderFactor);
        animStore.setStepIndex(stepIndex);
        animStore.setNSteps(field.n_steps);
        // A deformation scale the model can be seen at. Derived from the
        // displacement field and the model size, and only ever applied while the
        // user has not set a scale of their own.
        {
            const geom = session.active.mesh.geometry;
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
            // A fresh load (the caller moved the slider) was painted before the
            // scale above existed, so its influence is the bare slider value. Put
            // the mesh where the controls now say it is -- slider times scale --
            // or the first view of a deck that needed scaling showed it unscaled
            // until something happened to repaint it.
            if (sliderFactor !== undefined && session.active.mesh.morphTargetInfluences) {
                session.active.mesh.morphTargetInfluences[0] =
                    sliderFactor * useFeaAnimationStore.getState().scaleFactor;
            }
        }
        animStore.setFieldName(fieldName);
        if (reduction != null) animStore.setReduction(reduction);
        animStore.setColormap(colormap);
        // Through the same resolver the kernels used: a pinned range the legend
        // does not know about is a legend that disagrees with the picture beside
        // it, which is worse than no legend at all.
        const [legendMin, legendMax] = resolveContourRange(
            selectedResultRange(field, reduction ?? "magnitude"),
            useFeaAnimationStore.getState().contour,
        );
        const legendStore = useColorStore.getState();
        legendStore.setMin(legendMin);
        legendStore.setMax(legendMax);
        legendStore.setShowLegend(true);
    } else {
        // Field-less FEM mesh (model only): no results -> NO simulation session, so
        // SimulationControls + the results-only "show in data" action stay hidden. The
        // beam-solids toggle acts on the session's FEA mesh, not on a result session, so
        // it still works from the Scene > FEM panel.
        animStore.setSessionActive(false);
        animStore.setFieldName(null);
        animStore.setNSteps(1);
        animStore.setStepIndex(0);
        useColorStore.getState().setShowLegend(false);
    }

    // The colours and legend above assume nobody else owns the scene colouring.
    // A mode that does (capacity, inspect) may be on top of the owner stack: one
    // entered before this model loaded when the page opened straight into it,
    // or one that asked for this load itself. The tag taken at request time
    // says which; a load the mode did not ask for is set aside under it.
    noteFieldSourceLoaded(sourceName, colorOwner);

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
            // The influence is read at call time like the colormap, and for the
            // same reason: without it a step change repainted at the default of
            // 1 and dropped the slider and the warp scale the user had set.
            const {factor, scaleFactor} = useFeaAnimationStore.getState();
            await load_fea_streaming({
                sourceName,
                manifest,
                fieldName,
                stepIndex: newStepIndex,
                reduction,
                displacementScale: factor * scaleFactor,
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
    if (!capabilities.fea.supports("fetchManifest")) {
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
        manifest = await capabilities.fea.fetchManifest(scope, sourceName, {
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
                    url: (rel) => capabilities.files.blobUrl(scope, feaPrefix + rel.replace(/^\/+/, "")),
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
            sliderFactor: 1,
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

