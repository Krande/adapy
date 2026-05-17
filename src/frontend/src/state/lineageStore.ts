import {create} from 'zustand';
import * as THREE from 'three';

/**
 * In-memory CAD↔FEA lineage map.
 *
 * The adapy glTF extension (``ADA_EXT_data``) stamps every export with
 * the source ``Assembly.guid``. CAD exports also carry an
 * ``object_guids`` map (Beam/Plate name → adapy guid); FEA exports
 * carry ``SimGroup.parent_object_guid`` so each group of elements
 * meshed from a single beam knows which CAD object it came from.
 *
 * This store inverts those mappings at load time so that, given the
 * clicked element/object name and its owning file, we can answer
 * "what's the CAD parent?" / "which FEA elements were meshed from
 * me?" without walking back through the server or doing name matches
 * across files.
 *
 * Encoding: SimGroups for large beams switch from inline
 * ``members: [string]`` to a uint32 bufferView (see SimGroup schema).
 * We keep both representations in the store (``elementIds`` Uint32Array
 * + ``membersPrefix``) so the membership test stays O(N) but doesn't
 * have to materialize hundreds of thousands of JS string keys.
 */

export type LoadedCad = {
    fileName: string;
    root: THREE.Object3D;
    // Beam/Plate display name → adapy guid
    nameToGuid: Map<string, string>;
    // adapy guid → Beam/Plate display name (inverse, for jump-to-CAD)
    guidToName: Map<string, string>;
    // Optional per-object structured metadata (type/section/material/
    // thickness) when the GLB was written with
    // ``embed_object_metadata=True``. Lets the Properties panel skip
    // the server round-trip back to the source IFC.
    metadataByName: Map<string, any>;
};

type FeaGroup = {
    parentObjectGuid: string;
    // One of the two encodings is populated, never both:
    inlineMembers?: string[];
    bufferIds?: Uint32Array;
    membersPrefix?: string;
};

export type LoadedFea = {
    fileName: string;
    root: THREE.Object3D;
    groups: FeaGroup[];
    // Optional fast path for inline groups: element name → parent guid.
    // BufferView-backed groups are searched via ``groups`` because
    // building the inverse string map would defeat the size win.
    inlineNameToParent: Map<string, string>;
};

export type LineageEntry = {
    assemblyGuid: string;
    cad?: LoadedCad;
    fea: LoadedFea[];
};

export type LinkResult =
    | {kind: 'fea'; cad: {file: string; name: string; assemblyGuid: string}}
    | {kind: 'cad'; fem: Array<{file: string; elementNames: string[]; assemblyGuid: string}>}
    | null;

type RegisterCadPayload = {
    kind: 'cad';
    fileName: string;
    assemblyGuid: string;
    root: THREE.Object3D;
    objectGuids: Record<string, string>;
    objectMetadata?: Record<string, any> | null;
};

type RegisterFeaPayload = {
    kind: 'fea';
    fileName: string;
    assemblyGuid: string;
    root: THREE.Object3D;
    groups: FeaGroup[];
};

type LineageState = {
    // Keyed by assembly_guid so multiple files derived from the same
    // Assembly fold into a single entry.
    entries: Map<string, LineageEntry>;
    // file_name → assembly_guid, so unregister can locate the entry
    // without iterating every assembly.
    fileToAssembly: Map<string, string>;

    register: (payload: RegisterCadPayload | RegisterFeaPayload) => void;
    unregister: (fileName: string) => void;
    clear: () => void;

    findLink: (fileName: string | null, clickedName: string | null) => LinkResult;
    getAssemblyGuidForFile: (fileName: string) => string | null;
    getMetadata: (fileName: string | null, clickedName: string | null) => any | null;
};

export const useLineageStore = create<LineageState>((set, get) => ({
    entries: new Map(),
    fileToAssembly: new Map(),

    register: (payload) => {
        const {assemblyGuid, fileName} = payload;
        // Bail on empty guid — without it we have nothing to key on, and
        // mixing every unmarked file under "" would create false links.
        if (!assemblyGuid) return;
        set((state) => {
            const entries = new Map(state.entries);
            const fileToAssembly = new Map(state.fileToAssembly);
            const existing: LineageEntry = entries.get(assemblyGuid) ?? {
                assemblyGuid,
                fea: [],
            };
            // Replace any prior entry for this file under this assembly
            // (a reload should overwrite, not duplicate).
            const cleanedFea = existing.fea.filter((f) => f.fileName !== fileName);
            const next: LineageEntry = {
                assemblyGuid,
                cad: existing.cad?.fileName === fileName ? undefined : existing.cad,
                fea: cleanedFea,
            };
            if (payload.kind === 'cad') {
                const nameToGuid = new Map<string, string>();
                const guidToName = new Map<string, string>();
                for (const [name, guid] of Object.entries(payload.objectGuids)) {
                    if (!name || !guid) continue;
                    nameToGuid.set(name, guid);
                    guidToName.set(guid, name);
                }
                const metadataByName = new Map<string, any>();
                if (payload.objectMetadata) {
                    for (const [name, meta] of Object.entries(payload.objectMetadata)) {
                        if (name && meta) metadataByName.set(name, meta);
                    }
                }
                next.cad = {fileName, root: payload.root, nameToGuid, guidToName, metadataByName};
            } else {
                const inlineNameToParent = new Map<string, string>();
                for (const g of payload.groups) {
                    if (g.inlineMembers) {
                        for (const m of g.inlineMembers) {
                            inlineNameToParent.set(m, g.parentObjectGuid);
                        }
                    }
                }
                next.fea.push({
                    fileName,
                    root: payload.root,
                    groups: payload.groups,
                    inlineNameToParent,
                });
            }
            entries.set(assemblyGuid, next);
            // Maintain reverse map (replacing any prior assembly mapping
            // for this file, e.g. on reload).
            const prevAssembly = fileToAssembly.get(fileName);
            if (prevAssembly && prevAssembly !== assemblyGuid) {
                // File moved to a different assembly — clean its entry
                // in the old assembly bucket so we don't leave a ghost.
                const prevEntry = entries.get(prevAssembly);
                if (prevEntry) {
                    const stripped: LineageEntry = {
                        assemblyGuid: prevAssembly,
                        cad: prevEntry.cad?.fileName === fileName ? undefined : prevEntry.cad,
                        fea: prevEntry.fea.filter((f) => f.fileName !== fileName),
                    };
                    if (!stripped.cad && stripped.fea.length === 0) {
                        entries.delete(prevAssembly);
                    } else {
                        entries.set(prevAssembly, stripped);
                    }
                }
            }
            fileToAssembly.set(fileName, assemblyGuid);
            return {entries, fileToAssembly};
        });
    },

    unregister: (fileName) => {
        set((state) => {
            const assemblyGuid = state.fileToAssembly.get(fileName);
            if (!assemblyGuid) return state;
            const entries = new Map(state.entries);
            const fileToAssembly = new Map(state.fileToAssembly);
            const entry = entries.get(assemblyGuid);
            if (!entry) {
                fileToAssembly.delete(fileName);
                return {entries, fileToAssembly};
            }
            const next: LineageEntry = {
                assemblyGuid,
                cad: entry.cad?.fileName === fileName ? undefined : entry.cad,
                fea: entry.fea.filter((f) => f.fileName !== fileName),
            };
            if (!next.cad && next.fea.length === 0) {
                entries.delete(assemblyGuid);
            } else {
                entries.set(assemblyGuid, next);
            }
            fileToAssembly.delete(fileName);
            return {entries, fileToAssembly};
        });
    },

    clear: () => set({entries: new Map(), fileToAssembly: new Map()}),

    getAssemblyGuidForFile: (fileName) => {
        return get().fileToAssembly.get(fileName) ?? null;
    },

    getMetadata: (fileName, clickedName) => {
        if (!fileName || !clickedName) return null;
        const state = get();
        const assemblyGuid = state.fileToAssembly.get(fileName);
        if (!assemblyGuid) return null;
        const entry = state.entries.get(assemblyGuid);
        if (!entry?.cad || entry.cad.fileName !== fileName) return null;
        return entry.cad.metadataByName.get(clickedName) ?? null;
    },

    findLink: (fileName, clickedName) => {
        if (!fileName || !clickedName) return null;
        const state = get();
        const assemblyGuid = state.fileToAssembly.get(fileName);
        if (!assemblyGuid) return null;
        const entry = state.entries.get(assemblyGuid);
        if (!entry) return null;

        // Is this file the CAD side of the entry? If so, return its FEA
        // children — every loaded FEA in the same assembly that has
        // groups whose parent_object_guid matches the clicked object.
        if (entry.cad?.fileName === fileName) {
            const clickedGuid = entry.cad.nameToGuid.get(clickedName);
            if (!clickedGuid) return null;
            const fem = [];
            for (const feaFile of entry.fea) {
                const elementNames: string[] = [];
                for (const grp of feaFile.groups) {
                    if (grp.parentObjectGuid !== clickedGuid) continue;
                    if (grp.inlineMembers) {
                        elementNames.push(...grp.inlineMembers);
                    } else if (grp.bufferIds && grp.membersPrefix !== undefined) {
                        // Reconstruct names from packed IDs only when
                        // actually needed for selection.
                        const prefix = grp.membersPrefix;
                        for (let i = 0; i < grp.bufferIds.length; i++) {
                            elementNames.push(`${prefix}${grp.bufferIds[i]}`);
                        }
                    }
                }
                if (elementNames.length > 0) {
                    fem.push({file: feaFile.fileName, elementNames, assemblyGuid});
                }
            }
            return fem.length > 0 ? {kind: 'cad', fem} : null;
        }

        // Otherwise it's a FEA file. Walk its groups to find the one
        // owning ``clickedName``. Inline groups hit the fast Map; buffer
        // groups linear-scan their Uint32Array.
        const feaFile = entry.fea.find((f) => f.fileName === fileName);
        if (!feaFile) return null;
        let parentGuid = feaFile.inlineNameToParent.get(clickedName) ?? null;
        if (!parentGuid) {
            parentGuid = lookupBufferGroups(feaFile.groups, clickedName);
        }
        if (!parentGuid || !entry.cad) return null;
        const cadName = entry.cad.guidToName.get(parentGuid);
        if (!cadName) return null;
        return {
            kind: 'fea',
            cad: {file: entry.cad.fileName, name: cadName, assemblyGuid},
        };
    },
}));

/**
 * Strip a known prefix from ``clickedName`` and binary-test membership
 * against each buffer group's Uint32Array. Linear in total element
 * count per group — fine at expected sizes (one beam → ~10²–10³
 * elements). For multi-million-element parents, switch to sorted IDs
 * + binary search.
 */
function lookupBufferGroups(groups: FeaGroup[], clickedName: string): string | null {
    for (const grp of groups) {
        if (!grp.bufferIds || grp.membersPrefix === undefined) continue;
        const prefix = grp.membersPrefix;
        if (!clickedName.startsWith(prefix)) continue;
        const idStr = clickedName.slice(prefix.length);
        const id = Number(idStr);
        if (!Number.isFinite(id) || id < 0) continue;
        // Uint32Array.includes is a linear scan; acceptable at per-beam
        // mesh sizes. The cost is dwarfed by Three.js render loop.
        if (grp.bufferIds.includes(id)) {
            return grp.parentObjectGuid;
        }
    }
    return null;
}
