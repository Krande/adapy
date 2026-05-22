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
    modeIndex: number = 0,
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
    // Clamp the requested mode to whatever the field has — keeps
    // a caller asking for mode 7 on a 5-mode bake from crashing.
    const ACTIVE_MODE = Math.max(0, Math.min(parsed.steps.length - 1, modeIndex | 0));
    const modeStep = parsed.steps[ACTIVE_MODE];

    // Deformed positions = base + displacement vector at every node.
    const basePositions = positionAttr.array as Float32Array;
    const deformed = new Float32Array(basePositions.length);
    const mag = new Float32Array(n_points);
    let maxMag = 0;
    for (let v = 0; v < n_points; v++) {
        const dx = modeStep[v * n_components + 0];
        const dy = n_components >= 2 ? modeStep[v * n_components + 1] : 0;
        const dz = n_components >= 3 ? modeStep[v * n_components + 2] : 0;
        deformed[v * 3 + 0] = basePositions[v * 3 + 0] + dx;
        deformed[v * 3 + 1] = basePositions[v * 3 + 1] + dy;
        deformed[v * 3 + 2] = basePositions[v * 3 + 2] + dz;
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
