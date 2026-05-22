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

// Force a known mesh name on the assembled GLB so the animation
// track binds reliably after the GLTFExporter ↔ GLTFLoader roundtrip.
// The bake's `write_mesh_glb` doesn't guarantee a specific name (and
// `mesh.name` after import can be "" in some adapy bake variants —
// glTF nodes are name-optional), so we pin it here.
const ASSEMBLED_MESH_NAME = "node0";

function findFirstMesh(root: THREE.Object3D): THREE.Mesh | null {
    let found: THREE.Mesh | null = null;
    root.traverse((o) => {
        if (!found && (o as THREE.Mesh).isMesh) {
            found = o as THREE.Mesh;
        }
    });
    return found;
}

function pickDisplacementField(manifest: FeaManifest): FeaManifestField | null {
    if (!manifest || !Array.isArray(manifest.fields)) return null;
    // Prefer the canonical "displacement" category; fall back to
    // anything with a name starting with "U" (Abaqus convention) or
    // "DEPL" (Code Aster) so a manifest that's mis-categorised still
    // animates instead of silently emitting a static mesh.
    const byCategory = manifest.fields.find(
        (f) => f.category === "displacement" && f.blob,
    );
    if (byCategory) return byCategory;
    return manifest.fields.find(
        (f) =>
            f.blob &&
            (f.name_canonical?.toUpperCase().startsWith("U") ||
                f.name_canonical?.toUpperCase().includes("DEPL")),
    ) ?? null;
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
): Promise<Uint8Array> {
    // 1. Base mesh -------------------------------------------------------
    const meshBuf = await fetcher(manifest.mesh.url);
    const loader = new GLTFLoader();
    const gltf = await loader.parseAsync(meshBuf, "");
    const scene = gltf.scene;
    const mesh = findFirstMesh(scene);
    if (!mesh) {
        throw new Error(`fea bundle: no mesh in ${manifest.mesh.url}`);
    }
    // Pin the mesh name so the AnimationClip's `<name>.morphTargetInfluences`
    // track binds after the exporter ↔ loader roundtrip. Without this
    // an empty / writer-derived name can mean the track silently no-ops
    // and the user sees the un-deformed mesh under every mode.
    mesh.name = ASSEMBLED_MESH_NAME;

    // 2. Displacement field --------------------------------------------
    const field = pickDisplacementField(manifest);
    if (!field || !field.blob) {
        throw new Error(
            "fea bundle: manifest has no displacement field (category=displacement); " +
            "nothing to animate. Static mesh will still render but SimulationControls " +
            "won't surface.",
        );
    }

    const fieldBuf = await fetcher(field.blob.url);
    const parsed = parseFieldBlob(fieldBuf);
    const {n_steps, n_points, n_components} = parsed.header;

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

    // 3. Per-step morph targets ----------------------------------------
    // glTF morph targets are "additive" deltas by default in Three.js:
    // `position + sum_i (influence_i * morphAttribute_i)`. The bake's
    // AFBL blob already stores per-vertex displacement vectors, so the
    // delta IS the step values — no subtraction needed.
    //
    // We also build a parallel set of COLOUR morph targets so the
    // abaqus colormap travels in sync with the deformation: at
    // influence 0 the mesh shows the neutral base colour, at
    // influence 1 it shows the abaqus rainbow mapped from each
    // vertex's displacement magnitude (per-mode normalised). Three.js
    // interpolates both morph attributes linearly from the same
    // morphTargetInfluences[s] uniform, so position + colour stay in
    // lockstep without a custom shader.
    const positionMorphs: THREE.BufferAttribute[] = [];
    const colourMorphs: THREE.BufferAttribute[] = [];
    const rgbTmp = new Float32Array(3);
    for (let s = 0; s < n_steps; s++) {
        const step = parsed.steps[s];

        // Position delta + per-vertex displacement magnitude.
        const posDelta = new Float32Array(n_points * 3);
        const mag = new Float32Array(n_points);
        let maxMag = 0;
        for (let v = 0; v < n_points; v++) {
            const dx = step[v * n_components + 0];
            const dy = n_components >= 2 ? step[v * n_components + 1] : 0;
            const dz = n_components >= 3 ? step[v * n_components + 2] : 0;
            posDelta[v * 3 + 0] = dx;
            posDelta[v * 3 + 1] = dy;
            posDelta[v * 3 + 2] = dz;
            const m = Math.sqrt(dx * dx + dy * dy + dz * dz);
            mag[v] = m;
            if (m > maxMag) maxMag = m;
        }
        positionMorphs.push(new THREE.BufferAttribute(posDelta, 3));

        // Per-mode colour delta = abaqus(mag/maxMag) − BASE_VERTEX_COLOUR.
        // Three.js applies the delta with the same influence as the
        // position delta, so influence=0 → base, influence=1 →
        // abaqus(mag/maxMag).
        const colDelta = new Float32Array(n_points * 3);
        const invMax = maxMag > 0 ? 1 / maxMag : 0;
        for (let v = 0; v < n_points; v++) {
            const t = mag[v] * invMax;
            abaqus(t, rgbTmp, 0);
            colDelta[v * 3 + 0] = rgbTmp[0] - BASE_VERTEX_COLOUR[0];
            colDelta[v * 3 + 1] = rgbTmp[1] - BASE_VERTEX_COLOUR[1];
            colDelta[v * 3 + 2] = rgbTmp[2] - BASE_VERTEX_COLOUR[2];
        }
        colourMorphs.push(new THREE.BufferAttribute(colDelta, 3));
    }
    mesh.geometry.morphAttributes.position = positionMorphs;
    mesh.geometry.morphAttributes.color = colourMorphs;

    // Base colour attribute the morph deltas add into. Flat grey
    // everywhere so the un-deformed view reads as CAD-neutral.
    const baseColours = new Float32Array(n_points * 3);
    for (let v = 0; v < n_points; v++) {
        baseColours[v * 3 + 0] = BASE_VERTEX_COLOUR[0];
        baseColours[v * 3 + 1] = BASE_VERTEX_COLOUR[1];
        baseColours[v * 3 + 2] = BASE_VERTEX_COLOUR[2];
    }
    mesh.geometry.setAttribute(
        "color",
        new THREE.BufferAttribute(baseColours, 3),
    );

    // Switch the material(s) into vertexColors-aware mode so the
    // base + morph colour attributes actually paint the surface.
    const enableVertexColours = (m: THREE.Material) => {
        // MeshStandardMaterial / MeshPhongMaterial / etc. all have
        // `vertexColors: boolean`. Setting it after construction
        // requires needsUpdate so the shader recompiles.
        (m as THREE.MeshStandardMaterial).vertexColors = true;
        m.needsUpdate = true;
    };
    if (Array.isArray(mesh.material)) {
        mesh.material.forEach(enableVertexColours);
    } else if (mesh.material) {
        enableVertexColours(mesh.material);
    }
    // Three.js needs explicit influences + dictionary on the Mesh so
    // GLTFExporter writes the targets and the loader's animation
    // controller can find them.
    mesh.morphTargetInfluences = new Array(n_steps).fill(0);
    const dict: Record<string, number> = {};
    for (let s = 0; s < n_steps; s++) {
        // Manifest may carry per-step labels; fall back to mode_NN.
        const label = field.steps?.[s]?.label || `mode_${s + 1}`;
        dict[label] = s;
    }
    mesh.morphTargetDictionary = dict;

    // 4. One AnimationClip per step (oscillating mode shape) -----------
    // Each clip ramps its own morph influence 0 → 1 → 0 over 1.5 s
    // while pinning every other influence at 0. The same influence
    // drives both the position morph and the colour morph (see
    // step 3), so the abaqus rainbow strengthens and fades with the
    // deformation — visually intuitive and avoids the colour flicker
    // a `−1 → 0` negative-excursion phase would produce (colour
    // morph deltas have a defined sign, the mesh's mirror would
    // bleed into garbage RGB). Positive-only envelope is the trade-
    // off for syncing colours through Three.js' built-in morph mix.
    const clips: THREE.AnimationClip[] = [];
    const times = new Float32Array([0, 0.75, 1.5]);
    for (let active = 0; active < n_steps; active++) {
        const values = new Float32Array(times.length * n_steps);
        for (let t = 0; t < times.length; t++) {
            const env = [0, 1, 0][t];
            values[t * n_steps + active] = env;
        }
        const trackName = `${ASSEMBLED_MESH_NAME}.morphTargetInfluences`;
        const track = new THREE.NumberKeyframeTrack(trackName, Array.from(times), Array.from(values));
        const clipName = field.steps?.[active]?.label || `mode_${active + 1}`;
        clips.push(new THREE.AnimationClip(clipName, 1.5, [track]));
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
    if (manifest.mesh.edges_url) {
        try {
            const edgeBuf = await fetcher(manifest.mesh.edges_url);
            const idx = parseMeshEdges(edgeBuf);
            if (idx.length > 0) {
                const lineGeom = new THREE.BufferGeometry();
                // Share position + morph attributes with the face
                // mesh so deformation drives both rendering paths
                // from a single buffer (and a single set of
                // morphTargetInfluences). Three.js morph-shader
                // accepts this on Line materials.
                lineGeom.setAttribute("position", positionAttr);
                lineGeom.morphAttributes.position = positionMorphs;
                lineGeom.setIndex(new THREE.BufferAttribute(idx, 1));
                const lineMat = new THREE.LineBasicMaterial({
                    color: 0x111111,
                    depthTest: true,
                });
                const segments = new THREE.LineSegments(lineGeom, lineMat);
                segments.name = "fea-element-edges";
                segments.morphTargetInfluences = mesh.morphTargetInfluences;
                segments.morphTargetDictionary = dict;
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
                {binary: true, animations: clips},
            );
        },
    );
    if (!(result instanceof ArrayBuffer)) {
        throw new Error("fea bundle: GLTFExporter returned JSON, expected binary");
    }
    return new Uint8Array(result);
}
