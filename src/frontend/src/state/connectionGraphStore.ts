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

import {create} from "zustand";

/** One connection a selected member belongs to. */
export interface ConnectionRef {
    /** ada.Connection Part name — stable id used as keys in
     *  object_metadata / object_guids on the same DesignDataExtension. */
    name: string;
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
    /** Member name → connections it belongs to. */
    indexByMember: Map<string, ConnectionRef[]>;
    setIndex: (idx: Map<string, ConnectionRef[]>) => void;
    clearIndex: () => void;
};

export const useConnectionGraphStore = create<ConnectionGraphState>((set) => ({
    indexByMember: new Map(),
    setIndex: (indexByMember) => set({indexByMember}),
    clearIndex: () => set({indexByMember: new Map()}),
}));

/** Build the reverse member→connection index from the asset-level
 *  extension. Each design_object's `connections` array contributes
 *  one ConnectionRef per Connection, indexed under every beam, plate,
 *  and weld member name it owns (so a click on any one of them lands
 *  in the inspector's "Connections (N)" rollup). */
export function buildConnectionIndex(adaExt: unknown): Map<string, ConnectionRef[]> {
    const out = new Map<string, ConnectionRef[]>();
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

            const ref: ConnectionRef = {
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
            };

            // Index under every member this connection touches —
            // a click on any of them surfaces this connection in
            // the inspector. Welds count too because the user can
            // click a weld bead directly.
            const allMembers = [...beamNames, ...plateNames, ...weldNames];
            for (const m of allMembers) {
                const bucket = out.get(m);
                if (bucket) bucket.push(ref);
                else out.set(m, [ref]);
            }
        }
    }
    return out;
}
