import * as THREE from 'three';
import {useLineageStore} from '@/state/lineageStore';

/**
 * Pull lineage information out of a freshly-loaded GLB's ADA_EXT_data
 * extension and register it with the lineage store.
 *
 * CAD side: collects ``design_objects[*].object_guids`` into a single
 * name→guid map.
 * FEA side: walks ``simulation_objects[*].groups[*]`` that carry
 * ``parent_object_guid``. Small groups use inline ``members: [string]``;
 * large groups reference a uint32 bufferView via ``members_buffer_view``
 * which we resolve via the glTF parser to a Uint32Array.
 *
 * If neither side carries data, the registration is a no-op (the
 * lineage store ignores empty registrations).
 */
type Args = {
    // The loaded glTF (three.js GLTFLoader result, with ``parser``
    // exposed so we can fetch bufferViews).
    gltf: any;
    extension: any;
    fileName: string | null;
    root: THREE.Object3D;
};

export async function registerLineageFromExtension({gltf, extension, fileName, root}: Args): Promise<void> {
    if (!fileName) return;
    const assemblyGuid: string | undefined = extension?.assembly_guid;
    if (!assemblyGuid) return;

    const designObjects: any[] = extension.design_objects ?? [];
    const simulationObjects: any[] = extension.simulation_objects ?? [];

    // CAD-side registration: fold every design_object's object_guids
    // (and object_metadata, when ``embed_object_metadata=True`` at
    // export) into one map. Multiple design_objects on one GLB is
    // rare but possible (sub-assemblies).
    const objectGuids: Record<string, string> = {};
    const objectMetadata: Record<string, any> = {};
    for (const designObj of designObjects) {
        const og = designObj?.object_guids;
        if (og && typeof og === 'object') {
            for (const [name, guid] of Object.entries(og)) {
                if (typeof guid === 'string') objectGuids[name] = guid;
            }
        }
        const om = designObj?.object_metadata;
        if (om && typeof om === 'object') {
            for (const [name, meta] of Object.entries(om)) {
                if (meta && typeof meta === 'object') objectMetadata[name] = meta;
            }
        }
    }
    if (Object.keys(objectGuids).length > 0 || Object.keys(objectMetadata).length > 0) {
        useLineageStore.getState().register({
            kind: 'cad',
            fileName,
            assemblyGuid,
            root,
            objectGuids,
            objectMetadata: Object.keys(objectMetadata).length > 0 ? objectMetadata : null,
        });
    }

    // FEA-side registration: collect every SimGroup that carries a
    // parent_object_guid (the "lineage::*" groups emitted by
    // scene_from_fem.py) and resolve any bufferView references to
    // Uint32Arrays for click-time membership tests.
    const feaGroups: Array<{
        parentObjectGuid: string;
        inlineMembers?: string[];
        bufferIds?: Uint32Array;
        membersPrefix?: string;
    }> = [];
    for (const simObj of simulationObjects) {
        const groups: any[] = simObj?.groups ?? [];
        for (const grp of groups) {
            const parentGuid = grp?.parent_object_guid;
            if (!parentGuid) continue;
            if (Array.isArray(grp.members) && grp.members.length > 0) {
                feaGroups.push({
                    parentObjectGuid: parentGuid,
                    inlineMembers: grp.members,
                });
            } else if (
                typeof grp.members_buffer_view === 'number' &&
                typeof grp.members_prefix === 'string'
            ) {
                const ids = await resolveBufferViewAsUint32(gltf, grp.members_buffer_view);
                if (ids) {
                    feaGroups.push({
                        parentObjectGuid: parentGuid,
                        bufferIds: ids,
                        membersPrefix: grp.members_prefix,
                    });
                }
            }
        }
    }
    if (feaGroups.length > 0) {
        useLineageStore.getState().register({
            kind: 'fea',
            fileName,
            assemblyGuid,
            root,
            groups: feaGroups,
        });
    }
}

/**
 * Pull the binary content of a glTF bufferView and reinterpret it as a
 * little-endian Uint32Array (matching what
 * ``SceneConverter._consume_lineage_buffers`` writes).
 *
 * three's GLTFLoader exposes ``parser.getDependency('bufferView', idx)``
 * which returns the underlying ArrayBuffer (sliced to the bufferView's
 * byteOffset/byteLength). We construct a Uint32Array view over it
 * without copying.
 */
async function resolveBufferViewAsUint32(gltf: any, bufferViewIdx: number): Promise<Uint32Array | null> {
    try {
        const buf = await gltf.parser.getDependency('bufferView', bufferViewIdx);
        if (!(buf instanceof ArrayBuffer)) {
            console.warn(`lineage: bufferView ${bufferViewIdx} returned non-ArrayBuffer`);
            return null;
        }
        // The write side packs aligned uint32s (4 bytes each). If the
        // byteLength isn't divisible by 4, something's gone wrong.
        if (buf.byteLength % 4 !== 0) {
            console.warn(`lineage: bufferView ${bufferViewIdx} byteLength ${buf.byteLength} not uint32-aligned`);
            return null;
        }
        return new Uint32Array(buf);
    } catch (err) {
        console.warn(`lineage: failed to read bufferView ${bufferViewIdx}`, err);
        return null;
    }
}
