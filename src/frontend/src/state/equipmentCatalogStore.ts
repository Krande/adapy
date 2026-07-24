/**
 * Per-scope equipment-type & system-template catalogs (admin panels).
 *
 * Equipment types carry a bbox/mass/IFC-class/port list and an optional linked
 * CAD asset from which the bbox + a preview GLB are inferred by a worker job.
 * The catalog feeds the cellbuilder's add-equipment dropdown (by slug); system
 * templates feed the systems inspector. Editing is draft-based: a selected type
 * is fetched into an editable draft, mutated locally, then committed under
 * optimistic concurrency (revision).
 */
import {create} from "zustand";

import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";
import {
    viewerApi,
    type CatalogPort,
    type EquipmentTypeDetail,
    type EquipmentTypeDoc,
    type EquipmentTypeSummary,
    type PortCategory,
    type PortDirection,
    type SystemTemplateDetail,
    type SystemTemplateDoc,
    type SystemTemplateSummary,
} from "@/services/viewerApi";

function scopePart(): string {
    const scope = useScopeStore.getState().current;
    return scope ? scopeUrlPart(scope) : "user:me";
}

function errMsg(e: unknown): string {
    return e instanceof Error ? e.message : String(e);
}

const DEFAULT_PORT: CatalogPort = {
    name: "port",
    position: [0, 0, 0],
    direction_vector: [0, 0, 1],
    direction: "INOUT",
    category: "process",
};

export interface CatalogBboxJob {
    jobId: string;
    status: "queued" | "running" | "done" | "error";
    error?: string;
}

interface EquipmentCatalogState {
    // panel visibility
    equipmentPanelOpen: boolean;
    systemPanelOpen: boolean;
    toggleEquipmentPanel: () => void;
    toggleSystemPanel: () => void;

    // equipment catalog
    equipmentTypes: EquipmentTypeSummary[];
    selectedEquipmentId: string | null;
    equipmentDraft: EquipmentTypeDetail | null;
    equipmentDirty: boolean;
    equipmentBusy: boolean;
    equipmentError: string | null;
    bboxJob: CatalogBboxJob | null;

    // system template catalog
    systemTemplates: SystemTemplateSummary[];
    selectedSystemId: string | null;
    systemDraft: SystemTemplateDetail | null;
    systemDirty: boolean;
    systemBusy: boolean;
    systemError: string | null;

    // actions
    refreshEquipment: () => Promise<void>;
    createEquipment: (name: string) => Promise<void>;
    selectEquipment: (id: string | null) => Promise<void>;
    setEquipmentMeta: (patch: Partial<Pick<EquipmentTypeDetail, "name" | "slug" | "description">>) => void;
    setEquipmentDoc: (patch: Partial<EquipmentTypeDoc>) => void;
    addPort: () => void;
    updatePort: (index: number, patch: Partial<CatalogPort>) => void;
    removePort: (index: number) => void;
    saveEquipment: () => Promise<void>;
    deleteEquipment: (id: string) => Promise<void>;
    uploadCad: (file: File) => Promise<void>;
    copyCadFromScope: (sourceKey: string) => Promise<void>;
    inferBbox: () => Promise<void>;

    refreshSystems: () => Promise<void>;
    createSystem: (name: string) => Promise<void>;
    selectSystem: (id: string | null) => Promise<void>;
    setSystemMeta: (patch: Partial<Pick<SystemTemplateDetail, "name" | "slug" | "description">>) => void;
    setSystemDoc: (patch: Partial<SystemTemplateDoc>) => void;
    saveSystem: () => Promise<void>;
    deleteSystem: (id: string) => Promise<void>;
}

export const useEquipmentCatalogStore = create<EquipmentCatalogState>((set, get) => ({
    equipmentPanelOpen: false,
    systemPanelOpen: false,
    toggleEquipmentPanel: () => {
        const open = !get().equipmentPanelOpen;
        set({equipmentPanelOpen: open});
        if (open) void get().refreshEquipment();
    },
    toggleSystemPanel: () => {
        const open = !get().systemPanelOpen;
        set({systemPanelOpen: open});
        if (open) void get().refreshSystems();
    },

    equipmentTypes: [],
    selectedEquipmentId: null,
    equipmentDraft: null,
    equipmentDirty: false,
    equipmentBusy: false,
    equipmentError: null,
    bboxJob: null,

    systemTemplates: [],
    selectedSystemId: null,
    systemDraft: null,
    systemDirty: false,
    systemBusy: false,
    systemError: null,

    // ── equipment ────────────────────────────────────────────────────

    refreshEquipment: async () => {
        try {
            const types = await viewerApi.listEquipmentTypes(scopePart());
            set({equipmentTypes: types, equipmentError: null});
        } catch (e) {
            set({equipmentError: errMsg(e)});
        }
    },

    createEquipment: async (name: string) => {
        const trimmed = name.trim();
        if (!trimmed) return;
        set({equipmentBusy: true, equipmentError: null});
        try {
            const created = await viewerApi.createEquipmentType(scopePart(), trimmed);
            await get().refreshEquipment();
            await get().selectEquipment(created.id);
        } catch (e) {
            set({equipmentError: errMsg(e)});
        } finally {
            set({equipmentBusy: false});
        }
    },

    selectEquipment: async (id: string | null) => {
        if (id === null) {
            set({selectedEquipmentId: null, equipmentDraft: null, equipmentDirty: false, bboxJob: null});
            return;
        }
        set({selectedEquipmentId: id, equipmentError: null, bboxJob: null});
        try {
            const detail = await viewerApi.getEquipmentType(scopePart(), id);
            set({equipmentDraft: detail, equipmentDirty: false});
        } catch (e) {
            set({equipmentError: errMsg(e)});
        }
    },

    setEquipmentMeta: (patch) => {
        const d = get().equipmentDraft;
        if (!d) return;
        set({equipmentDraft: {...d, ...patch}, equipmentDirty: true});
    },

    setEquipmentDoc: (patch) => {
        const d = get().equipmentDraft;
        if (!d) return;
        set({equipmentDraft: {...d, doc: {...d.doc, ...patch}}, equipmentDirty: true});
    },

    addPort: () => {
        const d = get().equipmentDraft;
        if (!d) return;
        const n = d.doc.ports.length;
        const port: CatalogPort = {...DEFAULT_PORT, name: `port${n + 1}`};
        set({equipmentDraft: {...d, doc: {...d.doc, ports: [...d.doc.ports, port]}}, equipmentDirty: true});
    },

    updatePort: (index, patch) => {
        const d = get().equipmentDraft;
        if (!d) return;
        const ports = d.doc.ports.map((p, i) => (i === index ? {...p, ...patch} : p));
        set({equipmentDraft: {...d, doc: {...d.doc, ports}}, equipmentDirty: true});
    },

    removePort: (index) => {
        const d = get().equipmentDraft;
        if (!d) return;
        const ports = d.doc.ports.filter((_, i) => i !== index);
        set({equipmentDraft: {...d, doc: {...d.doc, ports}}, equipmentDirty: true});
    },

    saveEquipment: async () => {
        const d = get().equipmentDraft;
        if (!d) return;
        set({equipmentBusy: true, equipmentError: null});
        try {
            const res = await viewerApi.updateEquipmentType(
                scopePart(),
                d.id,
                {name: d.name, slug: d.slug, description: d.description, doc: d.doc},
                d.revision,
            );
            set({equipmentDraft: {...d, revision: res.revision}, equipmentDirty: false});
            await get().refreshEquipment();
        } catch (e) {
            set({equipmentError: errMsg(e)});
        } finally {
            set({equipmentBusy: false});
        }
    },

    deleteEquipment: async (id: string) => {
        set({equipmentBusy: true, equipmentError: null});
        try {
            await viewerApi.deleteEquipmentType(scopePart(), id);
            if (get().selectedEquipmentId === id) {
                set({selectedEquipmentId: null, equipmentDraft: null, equipmentDirty: false});
            }
            await get().refreshEquipment();
        } catch (e) {
            set({equipmentError: errMsg(e)});
        } finally {
            set({equipmentBusy: false});
        }
    },

    uploadCad: async (file: File) => {
        const d = get().equipmentDraft;
        if (!d) return;
        set({equipmentBusy: true, equipmentError: null});
        try {
            const buf = await file.arrayBuffer();
            const {cad_key} = await viewerApi.uploadEquipmentCad(scopePart(), d.id, file.name, buf);
            set({equipmentDraft: {...get().equipmentDraft!, cad_key}});
            await get().refreshEquipment();
        } catch (e) {
            set({equipmentError: errMsg(e)});
        } finally {
            set({equipmentBusy: false});
        }
    },

    copyCadFromScope: async (sourceKey: string) => {
        const d = get().equipmentDraft;
        if (!d || !sourceKey.trim()) return;
        set({equipmentBusy: true, equipmentError: null});
        try {
            const {cad_key} = await viewerApi.copyEquipmentCadFromScope(scopePart(), d.id, sourceKey.trim());
            set({equipmentDraft: {...get().equipmentDraft!, cad_key}});
            await get().refreshEquipment();
        } catch (e) {
            set({equipmentError: errMsg(e)});
        } finally {
            set({equipmentBusy: false});
        }
    },

    inferBbox: async () => {
        const d = get().equipmentDraft;
        if (!d) return;
        set({equipmentError: null});
        try {
            const {job_id} = await viewerApi.inferEquipmentBbox(scopePart(), d.id);
            set({bboxJob: {jobId: job_id, status: "queued"}});
            const poll = async () => {
                const cur = get().bboxJob;
                if (!cur || cur.jobId !== job_id) return; // superseded
                try {
                    const st = await viewerApi.convertStatus(job_id);
                    if (st.status === "done") {
                        set({bboxJob: {...cur, status: "done"}});
                        // refetch the draft so the inferred bbox + preview show
                        const sel = get().selectedEquipmentId;
                        if (sel === d.id && !get().equipmentDirty) {
                            const detail = await viewerApi.getEquipmentType(scopePart(), d.id);
                            set({equipmentDraft: detail});
                        }
                        return;
                    }
                    if (st.status === "error") {
                        set({bboxJob: {...cur, status: "error", error: st.error ?? "inference failed"}});
                        return;
                    }
                    set({bboxJob: {...cur, status: "running"}});
                    setTimeout(poll, 1500);
                } catch (e) {
                    set({bboxJob: {...cur, status: "error", error: errMsg(e)}});
                }
            };
            setTimeout(poll, 1500);
        } catch (e) {
            set({equipmentError: errMsg(e)});
        }
    },

    // ── systems ───────────────────────────────────────────────────────

    refreshSystems: async () => {
        try {
            const templates = await viewerApi.listSystemTemplates(scopePart());
            set({systemTemplates: templates, systemError: null});
        } catch (e) {
            set({systemError: errMsg(e)});
        }
    },

    createSystem: async (name: string) => {
        const trimmed = name.trim();
        if (!trimmed) return;
        set({systemBusy: true, systemError: null});
        try {
            const created = await viewerApi.createSystemTemplate(scopePart(), trimmed);
            await get().refreshSystems();
            await get().selectSystem(created.id);
        } catch (e) {
            set({systemError: errMsg(e)});
        } finally {
            set({systemBusy: false});
        }
    },

    selectSystem: async (id: string | null) => {
        if (id === null) {
            set({selectedSystemId: null, systemDraft: null, systemDirty: false});
            return;
        }
        set({selectedSystemId: id, systemError: null});
        try {
            const detail = await viewerApi.getSystemTemplate(scopePart(), id);
            set({systemDraft: detail, systemDirty: false});
        } catch (e) {
            set({systemError: errMsg(e)});
        }
    },

    setSystemMeta: (patch) => {
        const d = get().systemDraft;
        if (!d) return;
        set({systemDraft: {...d, ...patch}, systemDirty: true});
    },

    setSystemDoc: (patch) => {
        const d = get().systemDraft;
        if (!d) return;
        set({systemDraft: {...d, doc: {...d.doc, ...patch}}, systemDirty: true});
    },

    saveSystem: async () => {
        const d = get().systemDraft;
        if (!d) return;
        set({systemBusy: true, systemError: null});
        try {
            const res = await viewerApi.updateSystemTemplate(
                scopePart(),
                d.id,
                {name: d.name, slug: d.slug, description: d.description, doc: d.doc},
                d.revision,
            );
            set({systemDraft: {...d, revision: res.revision}, systemDirty: false});
            await get().refreshSystems();
        } catch (e) {
            set({systemError: errMsg(e)});
        } finally {
            set({systemBusy: false});
        }
    },

    deleteSystem: async (id: string) => {
        set({systemBusy: true, systemError: null});
        try {
            await viewerApi.deleteSystemTemplate(scopePart(), id);
            if (get().selectedSystemId === id) set({selectedSystemId: null, systemDraft: null, systemDirty: false});
            await get().refreshSystems();
        } catch (e) {
            set({systemError: errMsg(e)});
        } finally {
            set({systemBusy: false});
        }
    },
}));

export type {PortCategory, PortDirection};
