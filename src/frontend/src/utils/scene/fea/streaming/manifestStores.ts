// FEA streaming: what the manifest says about the model, into the stores.
//
// Owns: pushing the manifest's CAD<->FEA lineage, its FEA input concepts
// (masses / BCs / load scenarios) and its node/element sets into the stores
// that render them. A baked FEA-result GLB is geometry-only -- no ADA_EXT
// extension -- so none of these can be read off the loaded scene the way the
// CAD path reads them; the manifest is the only carrier. The stores are
// imported on demand: none of them is needed to draw the mesh.
//
// Inputs: the manifest, the source name, and the loaded GLB root (for the
// lineage's object root; falls back to the scene).

import * as THREE from "three";

import type {FeaManifest} from "@/services/viewerApi";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {findFirstMesh} from "./sceneMesh";

export async function registerManifestStores(args: {
    manifest: FeaManifest;
    sourceName: string;
    feaRoot: THREE.Object3D | null;
}): Promise<void> {
    const {manifest, sourceName, feaRoot} = args;
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
}
