// FEA streaming: opening the session.
//
// Owns: getting the result mesh GLB into the scene and the session handle
// built around it -- fetch the GLB and its AFEM selection sidecar, run it
// through `replace_model` with the selection userData installed, register it
// with the model state, hand the manifest's lineage / concepts / groups to
// their stores, find the mesh, snapshot its base positions, open the handle
// on the session and publish the bounding box. Runs once per source; a field
// or step change within a source never comes here.
//
// Inputs: a blob fetcher, the source name and manifest, and the caller's
// stage reporter + abort check. Writes `feaSession.active` and the session
// identity; attaching sidecars (beam solids, edges) is the loader's job.

import * as THREE from "three";

import {SceneOperations} from "@/flatbuffers/scene/scene-operations";
import type {FeaFetcher} from "@/services/fea/feaFetcher";
import {fetchMeshElements, type MeshElementEntry} from "@/services/feaMeshElements";
import type {FeaManifest} from "@/services/viewerApi";
import {useModelSessionStore, type FeaSessionHandle} from "@/state/modelSession";
import {useModelState} from "@/state/modelState";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {replace_model} from "@/utils/scene/handlers/update_scene_from_message";
import {registerManifestStores} from "./manifestStores";
import {findFirstMesh, installAfemUserData, snapshotBasePositions} from "./sceneMesh";
import {feaSession as session} from "./session";

/** Load the mesh GLB for `sourceName` into the scene and open the FEA session
 *  on it. Returns the handle now held by the session. */
export async function openFeaSession(args: {
    fetcher: FeaFetcher;
    sourceName: string;
    manifest: FeaManifest;
    stage: (label: string, progress: number) => void;
    throwIfAborted: () => void;
}): Promise<FeaSessionHandle> {
    const {fetcher, sourceName, manifest, stage, throwIfAborted} = args;
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
        // Lineage, FEM concepts and groups from the manifest into the stores
        // that render them. After setLoadedSourceName, whose subscription
        // would otherwise clear what this writes.
        await registerManifestStores({manifest, sourceName, feaRoot});
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

    const handle: FeaSessionHandle = {sourceName, manifest, mesh, basePositions};
    session.active = handle;
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
    return handle;
}
