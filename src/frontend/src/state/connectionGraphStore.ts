// Reverse member→connection index for the selection inspector.
//
// Replaces the older per-weld weldGraphStore: instead of "which welds
// touch this member", the inspector now shows "which connections this
// member is part of", and each connection rolls up its spec lineage
// + member roles + the per-connection weld set into one entry.
//
// Built once per GLB load from `DesignDataExtension.connections[]`
// (one entry per ada.Connection Part — see scene_from_part's
// _build_connection_entries). Read-only after build; the inspector
// looks up by member name with .get().
//
// PER LOADED SOURCE. Each model's connections are kept under the source it was loaded from
// (`register`), by both viewports: the three.js loader's (`modelState.registerLoadedSource`) and the
// three.js-free viewport's (`viewportPanelBridge`), which built none before -- a click on a weld
// there found no connection at all. `indexByMember` is every loaded source's, merged, so a detail
// loaded as an overlay of a model no longer replaces the model's own connections. A source that
// leaves the scene takes its connections with it.

import {create} from "zustand";

import {useModelState} from "./modelState";

/** One connection a selected member belongs to. */
export interface ConnectionRef {
    /** ada.Connection Part name — stable id used as keys in
     *  object_metadata / object_guids on the same DesignDataExtension. */
    name: string;
    /** The loaded source whose GLB lists it; absent from an index built without one. */
    file?: string;
    specName: string | null;
    specInputs: Record<string, unknown> | null;
    /** Role name ("incoming", "landing", …) → member names. Empty when
     *  the connection isn't spec-derived. */
    memberRoles: Record<string, string[]>;
    /** Flat lists for the "select all welds" / "select beams" links. */
    beamNames: string[];
    plateNames: string[];
    weldNames: string[];
}

type ConnectionGraphState = {
    /** Member name → connections it belongs to, over every loaded source. */
    indexByMember: Map<string, ConnectionRef[]>;
    /** Each loaded source's connections, as its GLB lists them. */
    byFile: Map<string, ConnectionRef[]>;
    /** Keep the connections `adaExt` lists as the source `file`'s, in place of any it had. */
    register: (file: string, adaExt: unknown) => void;
    /** Forget the source `file`'s connections. */
    unregister: (file: string) => void;
    setIndex: (idx: Map<string, ConnectionRef[]>) => void;
    clearIndex: () => void;
};

/** Index `refs` under every member each touches -- a click on any of them surfaces the connection
 *  in the inspector. Welds count too because the user can click a weld bead directly. */
function indexRefs(refs: Iterable<ConnectionRef>): Map<string, ConnectionRef[]> {
    const out = new Map<string, ConnectionRef[]>();
    for (const ref of refs) {
        for (const m of [...ref.beamNames, ...ref.plateNames, ...ref.weldNames]) {
            const bucket = out.get(m);
            if (!bucket) out.set(m, [ref]);
            else if (!bucket.includes(ref)) bucket.push(ref);
        }
    }
    return out;
}

const allRefs = (byFile: Map<string, ConnectionRef[]>) => [...byFile.values()].flat();

export const useConnectionGraphStore = create<ConnectionGraphState>((set, get) => ({
    indexByMember: new Map(),
    byFile: new Map(),
    register: (file, adaExt) => {
        const refs = connectionsOf(adaExt).map((r) => ({...r, file}));
        const byFile = new Map(get().byFile);
        if (refs.length) byFile.set(file, refs);
        else if (!byFile.delete(file)) return;
        set({byFile, indexByMember: indexRefs(allRefs(byFile))});
    },
    unregister: (file) => {
        if (!get().byFile.has(file)) return;
        const byFile = new Map(get().byFile);
        byFile.delete(file);
        set({byFile, indexByMember: indexRefs(allRefs(byFile))});
    },
    setIndex: (indexByMember) => set({indexByMember}),
    clearIndex: () => set({indexByMember: new Map(), byFile: new Map()}),
}));

// A source that leaves the scene, by whichever route, takes its connections with it.
useModelState.subscribe((s, prev) => {
    if (s.loadedSourceNames === prev.loadedSourceNames) return;
    for (const file of [...useConnectionGraphStore.getState().byFile.keys()]) {
        if (prev.loadedSourceNames.has(file) && !s.loadedSourceNames.has(file)) {
            useConnectionGraphStore.getState().unregister(file);
        }
    }
});

/** The connection called `name` of the loaded source `file`; with no file, of any source. */
export function findConnection(file: string | null | undefined, name: string): ConnectionRef | null {
    const byFile = useConnectionGraphStore.getState().byFile;
    const lists = file ? [byFile.get(file) ?? []] : [...byFile.values()];
    for (const refs of lists) {
        const hit = refs.find((r) => r.name === name);
        if (hit) return hit;
    }
    return null;
}

/** The connection the weld called `weldName` of the source `file` is a weld of: one that lists it
 *  among its welds. Null when none does. */
export function connectionOfWeld(
    index: Map<string, ConnectionRef[]>,
    file: string | null | undefined,
    weldName: string,
): ConnectionRef | null {
    const refs = (index.get(weldName) ?? []).filter((r) => r.weldNames.includes(weldName));
    return refs.find((r) => !file || !r.file || r.file === file) ?? null;
}

/** Every connection the asset-level extension lists: one ConnectionRef per entry of each
 *  design_object's `connections` array. */
export function connectionsOf(adaExt: unknown): ConnectionRef[] {
    const out: ConnectionRef[] = [];
    const designObjects = (adaExt as {design_objects?: unknown[]})?.design_objects;
    if (!Array.isArray(designObjects)) return out;

    for (const obj of designObjects) {
        const conns = (obj as {connections?: unknown[]})?.connections;
        if (!Array.isArray(conns)) continue;

        for (const raw of conns) {
            const entry = raw as Record<string, unknown> | null;
            if (!entry || typeof entry.name !== "string") continue;

            const beamNames = Array.isArray(entry.beam_names)
                ? entry.beam_names.filter((n): n is string => typeof n === "string")
                : [];
            const plateNames = Array.isArray(entry.plate_names)
                ? entry.plate_names.filter((n): n is string => typeof n === "string")
                : [];
            const weldNames = Array.isArray(entry.weld_names)
                ? entry.weld_names.filter((n): n is string => typeof n === "string")
                : [];

            const memberRoles: Record<string, string[]> = {};
            const rolesRaw = entry.member_roles as Record<string, unknown> | undefined;
            if (rolesRaw && typeof rolesRaw === "object") {
                for (const [role, names] of Object.entries(rolesRaw)) {
                    if (!Array.isArray(names)) continue;
                    memberRoles[role] = names.filter((n): n is string => typeof n === "string");
                }
            }

            out.push({
                name: entry.name,
                specName: typeof entry.spec_name === "string" ? entry.spec_name : null,
                specInputs:
                    entry.spec_inputs && typeof entry.spec_inputs === "object"
                        ? (entry.spec_inputs as Record<string, unknown>)
                        : null,
                memberRoles,
                beamNames,
                plateNames,
                weldNames,
            });
        }
    }
    return out;
}

/** Build the reverse member→connection index from the asset-level
 *  extension. Each design_object's `connections` array contributes
 *  one ConnectionRef per Connection, indexed under every beam, plate,
 *  and weld member name it owns (so a click on any one of them lands
 *  in the inspector's "Connections (N)" rollup). */
export function buildConnectionIndex(adaExt: unknown): Map<string, ConnectionRef[]> {
    return indexRefs(connectionsOf(adaExt));
}
