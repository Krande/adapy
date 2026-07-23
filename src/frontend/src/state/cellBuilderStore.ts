import {create} from "zustand";

import {ApiError, viewerApi, type ProceduralDoc} from "@/services/viewerApi";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {quantizeVec, type CellBox, type Vec3} from "@/utils/cellbuilder/snap";

// One box in the cellbuilder: either a space cell or an equipment unit.
export interface BuilderCell extends CellBox {
    id: string;
    name: string;
    kind: "cell" | "equipment";
    /** Archetype name (pump/tank/...) for equipment cells; from the
     * worker-advertised list. */
    equipmentType?: string;
}

export type CellBuilderMode = "idle" | "add-cell" | "add-equipment" | "drag-face";

export interface CompileJobState {
    jobId: string | null;
    derivedKey: string;
    status: "queued" | "running" | "done" | "error" | "cached";
    error?: string | null;
}

let _seq = 0;
const nextId = () => `cb_${++_seq}`;

function currentScopePart(): string {
    const scope = useScopeStore.getState().current;
    return scope ? scopeUrlPart(scope) : "user:me";
}

interface CellBuilderState {
    /** The procedural model open in the builder; null hides the whole tool
     * (top-row button included). */
    active: {modelId: string; name: string; revision: number} | null;
    cells: Record<string, BuilderCell>;
    mode: CellBuilderMode;
    gridStep: number;
    snapThreshold: number;
    dirty: boolean;
    autoCompile: boolean;
    committing: boolean;
    conflict: string | null;
    /** Worker-advertised equipment archetypes for the scope. */
    equipmentTypes: string[];
    selectedEquipmentType: string | null;
    compileJob: CompileJobState | null;
    panelVisible: boolean;

    open: (modelId: string, name: string, revision: number, doc: ProceduralDoc) => void;
    close: () => void;
    setMode: (mode: CellBuilderMode) => void;
    setPanelVisible: (v: boolean) => void;
    setGridStep: (v: number) => void;
    setSnapThreshold: (v: number) => void;
    setAutoCompile: (v: boolean) => void;
    setSelectedEquipmentType: (t: string | null) => void;
    addCell: (kind: "cell" | "equipment", origin: Vec3, size: Vec3) => void;
    updateCell: (id: string, patch: Partial<BuilderCell>) => void;
    removeCell: (id: string) => void;
    toDoc: () => ProceduralDoc;
    loadFromDoc: (doc: ProceduralDoc) => void;
    fetchEquipmentTypes: () => Promise<void>;
    commit: () => Promise<boolean>;
    compile: () => Promise<void>;
    viewResult: (derivedKey: string) => Promise<void>;
}

function cellsFromDoc(doc: ProceduralDoc): Record<string, BuilderCell> {
    const out: Record<string, BuilderCell> = {};
    for (const s of doc.spaces ?? []) {
        const id = nextId();
        out[id] = {
            id,
            name: String(s.NAME ?? id),
            kind: "cell",
            origin: [Number(s.X ?? 0), Number(s.Y ?? 0), Number(s.Z ?? 0)],
            size: [Number(s.DX ?? 1), Number(s.DY ?? 1), Number(s.DZ ?? 1)],
        };
    }
    for (const e of doc.equipments ?? []) {
        const id = nextId();
        out[id] = {
            id,
            name: String(e.NAME ?? id),
            kind: "equipment",
            equipmentType: typeof e.DESCRIPTION === "string" && e.DESCRIPTION ? e.DESCRIPTION : undefined,
            origin: [Number(e.X ?? 0), Number(e.Y ?? 0), Number(e.Z ?? 0)],
            size: [Number(e.LX ?? 1), Number(e.LY ?? 1), Number(e.LZ ?? 1)],
        };
    }
    return out;
}

function containingCellName(cells: Record<string, BuilderCell>, eq: BuilderCell): string {
    const cx = eq.origin[0] + eq.size[0] / 2;
    const cy = eq.origin[1] + eq.size[1] / 2;
    for (const c of Object.values(cells)) {
        if (c.kind !== "cell") continue;
        const inX = cx >= c.origin[0] && cx <= c.origin[0] + c.size[0];
        const inY = cy >= c.origin[1] && cy <= c.origin[1] + c.size[1];
        if (inX && inY) return c.name;
    }
    const first = Object.values(cells).find((c) => c.kind === "cell");
    return first ? first.name : "NoSpace";
}

export const useCellBuilderStore = create<CellBuilderState>((set, get) => ({
    active: null,
    cells: {},
    mode: "idle",
    gridStep: 0.1,
    snapThreshold: 0.25,
    dirty: false,
    autoCompile: true,
    committing: false,
    conflict: null,
    equipmentTypes: [],
    selectedEquipmentType: null,
    compileJob: null,
    panelVisible: false,

    open: (modelId, name, revision, doc) => {
        set({
            active: {modelId, name, revision},
            cells: cellsFromDoc(doc),
            mode: "idle",
            dirty: false,
            conflict: null,
            compileJob: null,
            panelVisible: true,
        });
        void get().fetchEquipmentTypes();
    },
    close: () => set({active: null, cells: {}, mode: "idle", dirty: false, panelVisible: false, compileJob: null}),
    setMode: (mode) => set({mode}),
    setPanelVisible: (panelVisible) => set({panelVisible}),
    setGridStep: (gridStep) => set({gridStep: Math.max(0, gridStep)}),
    setSnapThreshold: (snapThreshold) => set({snapThreshold: Math.max(0, snapThreshold)}),
    setAutoCompile: (autoCompile) => set({autoCompile}),
    setSelectedEquipmentType: (selectedEquipmentType) => set({selectedEquipmentType}),

    addCell: (kind, origin, size) =>
        set((s) => {
            const id = nextId();
            const count = Object.values(s.cells).filter((c) => c.kind === kind).length + 1;
            const eqType = kind === "equipment" ? (s.selectedEquipmentType ?? undefined) : undefined;
            const baseName = kind === "cell" ? "CELL" : (eqType ?? "EQ").toUpperCase();
            const cell: BuilderCell = {
                id,
                name: `${baseName}_${String(count).padStart(2, "0")}`,
                kind,
                equipmentType: eqType,
                origin: quantizeVec(origin, s.gridStep),
                size: quantizeVec(size, s.gridStep),
            };
            return {cells: {...s.cells, [id]: cell}, dirty: true, mode: "idle"};
        }),
    updateCell: (id, patch) =>
        set((s) => {
            const cur = s.cells[id];
            if (!cur) return s;
            return {cells: {...s.cells, [id]: {...cur, ...patch}}, dirty: true};
        }),
    removeCell: (id) =>
        set((s) => {
            const cells = {...s.cells};
            delete cells[id];
            return {cells, dirty: true};
        }),

    toDoc: () => {
        const cells = get().cells;
        const spaces = Object.values(cells)
            .filter((c) => c.kind === "cell")
            .map((c) => ({
                NAME: c.name,
                INCLUDE: true,
                X: c.origin[0],
                Y: c.origin[1],
                Z: c.origin[2],
                DX: c.size[0],
                DY: c.size[1],
                DZ: c.size[2],
            }));
        const equipments = Object.values(cells)
            .filter((c) => c.kind === "equipment")
            .map((c) => ({
                NAME: c.name,
                INCLUDE: true,
                SPACE_NAME: containingCellName(cells, c),
                SPACE_LOC: "ROOF",
                GLOBAL_COORDS: true,
                DESCRIPTION: c.equipmentType ?? null,
                X: c.origin[0],
                Y: c.origin[1],
                Z: c.origin[2],
                LX: c.size[0],
                LY: c.size[1],
                LZ: c.size[2],
                COGx: 0,
                COGy: 0,
                COGz: c.size[2] / 2,
                massDry: 0,
                massCont: 0,
            }));
        return {grid: {}, spaces, equipments, openings: []};
    },
    loadFromDoc: (doc) => set({cells: cellsFromDoc(doc), dirty: false}),

    fetchEquipmentTypes: async () => {
        try {
            const types = await viewerApi.proceduralEquipmentTypes(currentScopePart());
            set((s) => ({
                equipmentTypes: types,
                selectedEquipmentType:
                    s.selectedEquipmentType && types.includes(s.selectedEquipmentType)
                        ? s.selectedEquipmentType
                        : (types[0] ?? null),
            }));
        } catch (e) {
            console.warn("cellbuilder: equipment-types fetch failed", e);
            set({equipmentTypes: []});
        }
    },

    commit: async () => {
        const s = get();
        if (!s.active || s.committing) return false;
        set({committing: true, conflict: null});
        try {
            const res = await viewerApi.commitProceduralModel(
                currentScopePart(), s.active.modelId, s.toDoc(), s.active.revision,
            );
            set({active: {...s.active, revision: res.revision}, dirty: false, committing: false});
            if (get().autoCompile) {
                void get().compile();
            }
            return true;
        } catch (e) {
            if (e instanceof ApiError && e.status === 409) {
                set({
                    committing: false,
                    conflict: "Commit conflict: the model changed elsewhere. Reload it to pick up the latest revision.",
                });
            } else {
                set({committing: false, conflict: e instanceof Error ? e.message : String(e)});
            }
            return false;
        }
    },

    compile: async () => {
        const s = get();
        if (!s.active) return;
        if (s.dirty) {
            const ok = await get().commit();
            // commit() auto-compiles on success when enabled; avoid double-run
            if (ok && get().autoCompile) return;
            if (!ok) return;
        }
        const active = get().active;
        if (!active) return;
        try {
            const res = await viewerApi.compileProceduralModel(currentScopePart(), active.modelId);
            if (res.cached) {
                set({compileJob: {jobId: null, derivedKey: res.derived_key, status: "cached"}});
                return;
            }
            set({compileJob: {jobId: res.job_id, derivedKey: res.derived_key, status: "queued"}});
            const jobId = res.job_id!;
            const poll = async () => {
                const cur = get().compileJob;
                if (!cur || cur.jobId !== jobId) return; // superseded
                try {
                    const st = await viewerApi.convertStatus(jobId);
                    if (st.status === "done") {
                        set({compileJob: {...cur, status: "done"}});
                        return;
                    }
                    if (st.status === "error") {
                        set({compileJob: {...cur, status: "error", error: st.error ?? "compile failed"}});
                        return;
                    }
                    set({compileJob: {...cur, status: "running"}});
                    setTimeout(poll, 1500);
                } catch (e) {
                    set({compileJob: {...cur, status: "error", error: e instanceof Error ? e.message : String(e)}});
                }
            };
            setTimeout(poll, 1500);
        } catch (e) {
            set({
                compileJob: {
                    jobId: null,
                    derivedKey: "",
                    status: "error",
                    error: e instanceof Error ? e.message : String(e),
                },
            });
        }
    },

    viewResult: async (derivedKey: string) => {
        const active = get().active;
        const {load_glb_by_url_rest} = await import("@/utils/scene/handlers/view_file_object_from_server");
        await load_glb_by_url_rest(
            currentScopePart(), derivedKey, `procedural:${active ? active.name : derivedKey}`,
        );
    },
}));
