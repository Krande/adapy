// Client-side assembly of a static FEA artefact bundle into a
// consolidated GLB byte stream that the embed's `mountViewer` can
// consume directly.
//
// Replaces the per-mode static GLBs the legacy bake emitted, *without*
// requiring the streaming-FEA orchestrator (load_fea_streaming.ts)
// which is too tightly coupled to the standalone viewer's singleton
// scene + scope stores to be reusable from paradoc-embed.
//
// What goes in:
//
//   * `fea.mesh.glb` (un-deformed base geometry)
//   * `fea.<displacement>.bin` (AFBL blob with n_steps × n_points × ≥3 floats)
//   * `fea.mesh.edges.bin` (optional element-edge wireframe sidecar)
//
// What comes out:
//
//   A single binary GLB carrying:
//
//   * The base mesh geometry verbatim,
//   * One morph target per displacement step (the per-vertex delta
//     between un-deformed and the mode shape; loaders use this as
//     "additive" morph data),
//   * One glTF animation clip per step that ramps that morph's
//     influence 0 → 1 → 0 over 2 s, so the embed's animation
//     controller picks each up as a selectable clip and the
//     SimulationControls UI surfaces it,
//   * A LineSegments child sharing the same position + morph
//     attributes as the parent mesh so the wireframe deforms in
//     lockstep with each mode.
//
// The bytes are then routed through the existing `mountViewer(
// {modelBytes})` flow exactly like any other animated GLB. No embed
// rewiring needed.

import * as THREE from "three";
import {GLTFLoader} from "three/examples/jsm/loaders/GLTFLoader";
import {GLTFExporter} from "three/examples/jsm/exporters/GLTFExporter";

import {parseFieldBlob} from "@/services/feaFieldBlob";
import {parseMeshEdges} from "@/services/feaMeshEdges";
import {parseMeshElements} from "@/services/feaMeshElements";
import type {FeaFetcher} from "@/services/fea/feaFetcher";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {abaqus} from "./colormaps";

/** Base vertex colour when no mode is active — light neutral grey
 *  so the un-deformed mesh has a CAD-ish look. Per-mode colour morph
 *  targets store deltas relative to this; full mode influence (=1)
 *  brings the abaqus rainbow up at the deformation hotspots. */
const BASE_VERTEX_COLOUR = [0.7, 0.7, 0.7] as const;

/** Visual amplification factor applied to the eigen-mode
 *  displacement before adding it to the base positions. Kept at
 *  1.0 to render the solver's reported magnitudes faithfully —
 *  amplification belongs in a user-facing "warp scale" control
 *  rather than baked into the static figures. Mirrors the bake-
 *  side `WARP_SCALE` in `build_verification_report.py`. */
const WARP_SCALE = 1.0;

// Force a known mesh name on the assembled GLB so the animation
// track binds reliably after the GLTFExporter ↔ GLTFLoader roundtrip.
// The bake's `write_mesh_glb` doesn't guarantee a specific name (and
// `mesh.name` after import can be "" in some adapy bake variants —
// glTF nodes are name-optional), so we pin it here.
const ASSEMBLED_MESH_NAME = "node0";

/**
 * Find the first renderable primitive (Mesh, LineSegments, Line, or
 * Points) in a loaded glTF scene that we can hang morph targets on.
 *
 * The bake emits a single `fea.mesh.glb` per case, but its top-level
 * primitive mode varies by element type:
 *   * 2D faces (shell o1, solid o2 surface tris) → TRIANGLES → Mesh
 *   * 1D line beams                              → LINES   → LineSegments
 *   * Element types `write_mesh_glb` doesn't know how to face-extract
 *     (e.g. 2nd-order shells until adapy gains that path) → POINTS
 *     → Points
 *
 * All four are valid morph + vertex-colour carriers in Three.js, so we
 * accept any of them. Strict Mesh-only worked for shell o1 / solid o2
 * but exploded on Code Aster line o1 + shell o2 cases with
 * `no mesh in fea.mesh.glb`.
 */
function findFirstRenderable(root: THREE.Object3D): (THREE.Object3D & {
    geometry: THREE.BufferGeometry;
    material: THREE.Material | THREE.Material[];
    morphTargetInfluences?: number[];
    morphTargetDictionary?: Record<string, number>;
}) | null {
    let found: any = null;
    root.traverse((o) => {
        if (found) return;
        const obj = o as any;
        if (obj.isMesh || obj.isLineSegments || obj.isLine || obj.isPoints) {
            found = obj;
        }
    });
    return found;
}

/** All displacement-flavoured fields in the manifest, in
 *  manifest order. Calculix / Abaqus ship one field ("U") with N
 *  steps; Code Aster ships N fields ("result__DEPL[N]") with
 *  1 step each. The unified "mode i" is found by walking the
 *  flattened (field, step) list from `pickModeEntry` below. */
function listDisplacementFields(manifest: FeaManifest): FeaManifestField[] {
    if (!manifest || !Array.isArray(manifest.fields)) return [];
    return manifest.fields.filter((f) => {
        if (!f.blob) return false;
        const name = (f.name_canonical || "").toUpperCase();
        return (
            f.category === "displacement" ||
            name === "U" ||
            name.startsWith("U[") ||
            name.includes("DEPL") ||
            name.includes("DISPLACEMENT")
        );
    });
}

/** Pick the (field, step_within_field) pair corresponding to a
 *  caller-supplied global mode index. Walks displacement fields in
 *  manifest order and accumulates each field's n_steps until the
 *  index lands. Returns null if the manifest has no displacement
 *  fields at all. */
function pickModeEntry(
    manifest: FeaManifest,
    globalModeIndex: number,
): {field: FeaManifestField; stepIndex: number} | null {
    const fields = listDisplacementFields(manifest);
    if (fields.length === 0) return null;
    let totalSeen = 0;
    for (const f of fields) {
        const n = Math.max(1, f.n_steps | 0);
        if (globalModeIndex < totalSeen + n) {
            return {field: f, stepIndex: globalModeIndex - totalSeen};
        }
        totalSeen += n;
    }
    // Out of range — clamp to the last (field, last-step).
    const last = fields[fields.length - 1];
    return {field: last, stepIndex: Math.max(0, (last.n_steps | 0) - 1)};
}

/**
 * Fetch + assemble the FEA artefact bundle into a single binary GLB.
 *
 * Pure asynchronous transform — touches the network only through
 * `fetcher`, mutates no scene state, returns the consolidated bytes
 * for whoever wants to mount them. paradoc-embed feeds them straight
 * into `mountViewer({modelBytes})`; the standalone viewer can do the
 * same if it ever wants the consolidated-GLB UX over its current
 * streaming path.
 */
export async function assembleAnimatedFeaGlb(
    fetcher: FeaFetcher,
    manifest: FeaManifest,
    modeIndex: number = 0,
): Promise<Uint8Array> {
    // 1. Base mesh -------------------------------------------------------
    const meshBuf = await fetcher(manifest.mesh.url);
    const loader = new GLTFLoader();
    const gltf = await loader.parseAsync(meshBuf, "");
    const scene = gltf.scene;
    const mesh = findFirstRenderable(scene);
    if (!mesh) {
        throw new Error(`fea bundle: no renderable primitive in ${manifest.mesh.url}`);
    }
    // Pin the mesh name so the AnimationClip's `<name>.morphTargetInfluences`
    // track binds after the exporter ↔ loader roundtrip. Without this
    // an empty / writer-derived name can mean the track silently no-ops
    // and the user sees the un-deformed mesh under every mode.
    mesh.name = ASSEMBLED_MESH_NAME;

    // 2. Displacement field (mode lookup) ------------------------------
    // For a caller-supplied global modeIndex, walk the manifest's
    // displacement fields to find which field + which step
    // corresponds. Both Calculix (1 field × N steps) and Code Aster
    // (N fields × 1 step each) flatten to the same global index.
    const entry = pickModeEntry(manifest, Math.max(0, modeIndex | 0));
    if (!entry || !entry.field.blob) {
        throw new Error(
            "fea bundle: manifest has no displacement fields; nothing to deform.",
        );
    }
    const field = entry.field;

    const fieldBuf = await fetcher(field.blob.url);
    const parsed = parseFieldBlob(fieldBuf);
    const {n_points, n_components} = parsed.header;
    // Step index within THIS field (already clamped by pickModeEntry).
    const stepIndexInField = Math.min(entry.stepIndex, parsed.steps.length - 1);

    const positionAttr = mesh.geometry.getAttribute("position") as THREE.BufferAttribute;
    if (!positionAttr) {
        throw new Error("fea bundle: base mesh has no position attribute");
    }
    if (positionAttr.count !== n_points) {
        // The bake guarantees these match (same mesh feeds both the
        // GLB writer and the field-blob writer); a mismatch means
        // the manifest's mesh URL points at the wrong file. Surface
        // explicitly so the user sees the bake error, not a silent
        // half-rendered mode shape.
        throw new Error(
            `fea bundle: vertex count mismatch — mesh has ${positionAttr.count} ` +
            `vertices, displacement field has ${n_points}. Bundle is corrupt.`,
        );
    }

    // 3. Static mode-1 deformation + abaqus vertex colours -----------
    // First cut: bake the first mode (or the first step) into the
    // GLB as a STATIC deformed mesh + per-vertex abaqus colours.
    //
    // Why not glTF morph targets + animation clips: that path looked
    // promising on paper but turned out brittle in practice — the
    // animation track binding through GLTFExporter ↔ GLTFLoader was
    // unreliable across the embed's CustomBatchedMesh conversion, and
    // when clips DID register the embed lit up the legacy
    // AnimationControls panel instead of SimulationControls (which is
    // what FEA mode shapes should drive). Proper SimulationControls
    // integration needs `feaAnimationStore.setMesh(...)` + scene-
    // graph references the standalone viewer's `load_fea_streaming`
    // owns; that's the right next step and lives in a separate piece
    // of work.
    //
    // For now we show ONE deformed mode statically — same data, no
    // animation, no controls UI. Mode 1 is the lowest-frequency mode
    // and the most visually informative; subsequent modes can be
    // selected once the SimControls integration lands.
    const modeStep = parsed.steps[stepIndexInField];

    // Deformed positions = base + displacement vector at every node.
    const basePositions = positionAttr.array as Float32Array;
    const deformed = new Float32Array(basePositions.length);
    const mag = new Float32Array(n_points);
    let maxMag = 0;
    for (let v = 0; v < n_points; v++) {
        const dx = modeStep[v * n_components + 0] * WARP_SCALE;
        const dy = n_components >= 2 ? modeStep[v * n_components + 1] * WARP_SCALE : 0;
        const dz = n_components >= 3 ? modeStep[v * n_components + 2] * WARP_SCALE : 0;
        deformed[v * 3 + 0] = basePositions[v * 3 + 0] + dx;
        deformed[v * 3 + 1] = basePositions[v * 3 + 1] + dy;
        deformed[v * 3 + 2] = basePositions[v * 3 + 2] + dz;
        // Magnitude still computed on the scaled displacement so the
        // abaqus colourmap normalises to the same per-mode max we
        // actually rendered (otherwise the colour scale and the
        // deformation scale would disagree).
        const m = Math.sqrt(dx * dx + dy * dy + dz * dz);
        mag[v] = m;
        if (m > maxMag) maxMag = m;
    }
    mesh.geometry.setAttribute(
        "position",
        new THREE.BufferAttribute(deformed, 3),
    );

    // Per-vertex abaqus rainbow on displacement magnitude.
    const colours = new Float32Array(n_points * 3);
    const rgbTmp = new Float32Array(3);
    const invMax = maxMag > 0 ? 1 / maxMag : 0;
    for (let v = 0; v < n_points; v++) {
        abaqus(mag[v] * invMax, rgbTmp, 0);
        colours[v * 3 + 0] = rgbTmp[0];
        colours[v * 3 + 1] = rgbTmp[1];
        colours[v * 3 + 2] = rgbTmp[2];
    }
    mesh.geometry.setAttribute(
        "color",
        new THREE.BufferAttribute(colours, 3),
    );

    // Switch material(s) into vertexColors-aware mode so the colour
    // attribute actually paints the surface.
    const enableVertexColours = (m: THREE.Material) => {
        (m as THREE.MeshStandardMaterial).vertexColors = true;
        m.needsUpdate = true;
    };
    if (Array.isArray(mesh.material)) {
        mesh.material.forEach(enableVertexColours);
    } else if (mesh.material) {
        enableVertexColours(mesh.material);
    }

    // 5a. Per-element draw ranges (AFEM) ------------------------------
    // The mesh enters the embed's `prepareLoadedModel` pipeline, which
    // converts a Mesh into a `CustomBatchedMesh` for per-element pick +
    // highlight. The conversion is driven by
    // `gltf_scene.userData.draw_ranges_<meshName>` + `id_hierarchy`,
    // which the bake-job writer normally installs via a prepareHook on
    // `setupModelLoaderAsync`. paradoc-embed loads through
    // `mountViewer` (no hook), so we install the same userData on the
    // exported scene; GLTFExporter writes it as `extras` and the
    // GLTFLoader puts it back on `userData` on import. End-to-end:
    // FEA mesh clicks resolve to single elements, not the whole beam.
    if (manifest.mesh.elements_url) {
        try {
            const entries = parseMeshElements(await fetcher(manifest.mesh.elements_url));
            const ranges: Record<string, [number, number]> = {};
            const hierarchy: Record<string, [string, string | number]> = {};
            const ROOT_KEY = "fea-elements-root";
            hierarchy[ROOT_KEY] = ["FEA elements", "*"];
            for (const e of entries) {
                if (e.triCount > 0) {
                    const rid = `E${e.label}`;
                    ranges[rid] = [e.triStart * 3, e.triCount * 3];
                    hierarchy[rid] = [rid, ROOT_KEY];
                }
            }
            // setupModelLoaderAsync reads
            // `gltf_scene.userData.draw_ranges_<meshName>`. Match the
            // pinned mesh name so the right key is found.
            scene.userData[`draw_ranges_${ASSEMBLED_MESH_NAME}`] = ranges;
            scene.userData["id_hierarchy"] = hierarchy;
        } catch (err) {
            // Selection wiring is best-effort — without AFEM the picker
            // still resolves clicks to the whole-mesh level, which is
            // what the user already sees today on bundles that don't
            // ship an elements sidecar.
            // eslint-disable-next-line no-console
            console.warn("[fea-assemble] elements sidecar load failed:", err);
        }
    }

    // 5b. Element-edge wireframe (optional) ----------------------------
    // Shares the deformed `positionAttr` we just installed, so the
    // wireframe automatically follows the mode shape.
    if (manifest.mesh.edges_url) {
        try {
            const edgeBuf = await fetcher(manifest.mesh.edges_url);
            const idx = parseMeshEdges(edgeBuf);
            if (idx.length > 0) {
                const lineGeom = new THREE.BufferGeometry();
                lineGeom.setAttribute(
                    "position",
                    mesh.geometry.getAttribute("position"),
                );
                lineGeom.setIndex(new THREE.BufferAttribute(idx, 1));
                const lineMat = new THREE.LineBasicMaterial({
                    color: 0x111111,
                    depthTest: true,
                });
                const segments = new THREE.LineSegments(lineGeom, lineMat);
                segments.name = "fea-element-edges";
                mesh.add(segments);
            }
        } catch (err) {
            // Edges are decorative — if the sidecar is missing or
            // malformed, render the bare mesh.
            // eslint-disable-next-line no-console
            console.warn("[fea-assemble] edges load failed:", err);
        }
    }

    // 6. Export to binary GLB ------------------------------------------
    const exporter = new GLTFExporter();
    const result = await new Promise<ArrayBuffer | Record<string, unknown>>(
        (resolve, reject) => {
            exporter.parse(
                scene,
                (r) => resolve(r),
                (err) => reject(err),
                {binary: true},
            );
        },
    );
    if (!(result instanceof ArrayBuffer)) {
        throw new Error("fea bundle: GLTFExporter returned JSON, expected binary");
    }
    return new Uint8Array(result);
}
