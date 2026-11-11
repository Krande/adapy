import {useLineageStore, type LineageRoot} from '@/state/lineageStore';

/**
 * Pull lineage information out of a freshly-loaded GLB's ADA_EXT_data
 * extension and register it with the lineage store.
 *
 * CAD side: collects ``design_objects[*].object_guids`` into a single
 * name→guid map.
 * FEA side: walks ``simulation_objects[*].groups[*]`` that carry
 * ``parent_object_guid``. Small groups use inline ``members: [string]``;
 * large groups reference a uint32 bufferView via ``members_buffer_view``
 * which the caller's ``bufferView`` reads, to a Uint32Array.
 *
 * If neither side carries data, the registration is a no-op (the
 * lineage store ignores empty registrations).
 *
 * No three.js here: both viewports register through it, each with its
 * own way to read a buffer view.
 */
type Args = {
    // The bytes of one of the GLB's buffer views (a view or its own
    // buffer), or null when it cannot be read.
    bufferView: (index: number) => Promise<ArrayBuffer | Uint8Array | null> | ArrayBuffer | Uint8Array | null;
    extension: any;
    fileName: string | null;
    root: LineageRoot;
};

export async function registerLineageFromExtension({bufferView, extension, fileName, root}: Args): Promise<void> {
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
                const ids = await resolveBufferViewAsUint32(bufferView, grp.members_buffer_view);
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
 * A glTF bufferView's bytes as a little-endian Uint32Array (matching
 * what ``SceneConverter._consume_lineage_buffers`` writes). No copy when
 * the bytes start on a four-byte boundary of their buffer.
 */
async function resolveBufferViewAsUint32(bufferView: Args['bufferView'], bufferViewIdx: number): Promise<Uint32Array | null> {
    try {
        const buf = await bufferView(bufferViewIdx);
        if (!buf) {
            console.warn(`lineage: bufferView ${bufferViewIdx} could not be read`);
            return null;
        }
        // The write side packs aligned uint32s (4 bytes each). If the
        // byteLength isn't divisible by 4, something's gone wrong.
        if (buf.byteLength % 4 !== 0) {
            console.warn(`lineage: bufferView ${bufferViewIdx} byteLength ${buf.byteLength} not uint32-aligned`);
            return null;
        }
        if (buf instanceof ArrayBuffer) return new Uint32Array(buf);
        if (buf.byteOffset % 4 === 0) return new Uint32Array(buf.buffer, buf.byteOffset, buf.byteLength / 4);
        return new Uint32Array(buf.slice().buffer);
    } catch (err) {
        console.warn(`lineage: failed to read bufferView ${bufferViewIdx}`, err);
        return null;
    }
}
