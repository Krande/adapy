import React, {useEffect, useMemo, useRef, useState} from "react";
import {viewerApi, type ProceduralModelSummary, type ProceduralTemplate} from "@/services/viewerApi";
import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import {useServerInfoStore} from "@/state/serverInfoStore";
import {useCellBuilderStore, cellsFromDoc} from "@/state/cellBuilderStore";
import {
    companionSourceName,
    useCompanionModelStore,
    type CompanionRep,
} from "@/state/companionModelStore";
import {cellsWidthX, nextModelOffsetX} from "@/utils/cellbuilder/modelPlacement";
import {unload_any_source} from "@/utils/scene/handlers/unload_any_source";
import {
    getSourceOffset,
    placeNextToExisting,
    resetSourceOffset,
    setSourceOffset,
} from "@/utils/scene/handlers/model_translate";
import {writeToClipboard} from "@/utils/clipboard/copySelectionNames";
import {buildProceduralMenuItems} from "../storageMenuItems";
import type {FolderPicker} from "./useUploads";

// Procedural cell models in the storage browser: pg-backed pseudo-entries
// listed inside the file tree. Each row refers to the single postgres source,
// not a blob — no rename/move through storage; delete archives the row
// server-side. Also the start-from templates and the companion (side-by-side)
// model handling.
export function useProceduralModels(p: {
    scopeKey: string;
    loadedSourceNames: ReadonlySet<string>;
    canMutate: boolean;
    setPicker: (picker: FolderPicker | null) => void;
    /** Close the "+" menu (a template pick swaps it for the template list). */
    setPlusOpen: (open: boolean) => void;
}) {
    const {scopeKey, loadedSourceNames, canMutate, setPicker, setPlusOpen} = p;

    // Procedural cell models: pg-backed pseudo-entries listed above the file
    // tree. Each row refers to the single postgres source, not a blob — no
    // rename/move; delete archives the row server-side.
    const [proceduralModels, setProceduralModels] = useState<ProceduralModelSummary[]>([]);
    const activeProcedural = useCellBuilderStore((s) => s.active?.modelId ?? null);
    // Engine-picker prompt raised by the store when an imported workbook has no
    // _ADA_META engine (hand-made / legacy). Rendered here because import is now
    // triggered from the + menu, not the cellbuilder panel.
    const importPrompt = useCellBuilderStore((s) => s.importPrompt);
    const importEngines = useCellBuilderStore((s) => s.engines);
    const refreshProceduralModels = React.useCallback(async () => {
        try {
            setProceduralModels(await viewerApi.listProceduralModels(scopeKey));
        } catch {
            // shared-only deployments (503) or older APIs: hide the section
            setProceduralModels([]);
        }
    }, [scopeKey]);
    useEffect(() => {
        void refreshProceduralModels();
    }, [refreshProceduralModels]);
    // A model becoming active (created / opened / imported) may be new to the
    // list — refresh so an Excel-imported model appears without a manual reload.
    useEffect(() => {
        if (activeProcedural) void refreshProceduralModels();
    }, [activeProcedural, refreshProceduralModels]);

    // Start-from templates for the "New model from template" dropdown — the
    // union of the demo templates advertised by every currently-live worker
    // (base worker → adapy-default; a capability worker → its loft demos, etc.).
    // Refetched on scope change; empty when no workers are up.
    const [allTemplates, setAllTemplates] = useState<ProceduralTemplate[]>([]);
    const [templatesOpen, setTemplatesOpen] = useState(false);
    const templatesBtnRef = useRef<HTMLButtonElement | null>(null);
    const refreshTemplates = React.useCallback(async () => {
        try {
            setAllTemplates(await viewerApi.listProceduralTemplates(scopeKey));
        } catch {
            setAllTemplates([]);
        }
    }, [scopeKey]);
    useEffect(() => {
        void refreshTemplates();
    }, [refreshTemplates]);

    const openProceduralModel = async (m: ProceduralModelSummary, opts?: {collapsePanel?: boolean}) => {
        try {
            const detail = await viewerApi.getProceduralModel(scopeKey, m.id);
            useCellBuilderStore.getState().open(detail.id, detail.name, detail.revision, detail.doc);
            // Opening from a row means "show me this model", so the storage
            // overview gets out of the way. Switching the ACTIVE model from the
            // menu does not: the operator is arranging several and needs the
            // list to keep arranging them.
            if (opts?.collapsePanel !== false) {
                useServerInfoStore.getState().setShowServerInfoBox(false);
            }
        } catch (e) {
            window.alert(`Failed to open procedural model: ${e instanceof Error ? e.message : e}`);
        }
    };

    // One scene source per model, named from the model rather than from
    // whatever happened to be active when it was loaded. That is what lets a
    // row answer "is my result in the scene?" — and for the active model it is
    // the same string the cellbuilder's own compile path produces.
    const proceduralSourceName = (m: ProceduralModelSummary) => `procedural:${m.name}`;
    const isProceduralLoaded = (m: ProceduralModelSummary) =>
        // In the scene by any route: the edited model, a companion drawing its
        // topology, or a companion whose compiled result is loaded.
        activeProcedural === m.id ||
        !!companions[m.id] ||
        loadedSourceNames.has(proceduralSourceName(m));

    // The checkbox on a model row means what it means on a file row: put this
    // in the scene. Loading also makes the model ACTIVE — you ticked it to work
    // on it — while anything already loaded stays in the scene and simply stops
    // being the one being edited. Several visible, one active.
    // Companion = present in the scene but not the model being edited. The
    // first model loaded becomes ACTIVE (there is nothing to compare it with
    // yet); each subsequent one joins as a companion, placed clear of whatever
    // is already there.
    const companions = useCompanionModelStore((st) => st.companions);

    const addCompanion = async (m: ProceduralModelSummary) => {
        const detail = await viewerApi.getProceduralModel(scopeKey, m.id);
        const cells = Object.values(cellsFromDoc(detail.doc));
        const placed = Object.values(useCompanionModelStore.getState().companions).map((c) => ({
            offsetX: c.offsetX,
            width: cellsWidthX(c.cells),
        }));
        const active = useCellBuilderStore.getState();
        if (active.active) {
            placed.push({offsetX: 0, width: cellsWidthX(Object.values(active.cells))});
        }
        useCompanionModelStore.getState().add({
            modelId: m.id,
            name: m.name,
            cells,
            rep: "topology",
            offsetX: nextModelOffsetX(placed, cellsWidthX(cells)),
            latestGlbKey: m.latest_glb_key ?? null,
        });
    };

    /** Promote a model to the edited one, demoting whatever held that role.
     *
     * A SWAP, not a replace: the model you were editing stays in the scene as a
     * companion at the offset it already had, so "make active" changes what is
     * editable without changing what is visible. Before companions existed this
     * necessarily closed the previous model, which is what made two models look
     * impossible. */
    const makeProceduralActive = async (m: ProceduralModelSummary) => {
        const cb = useCellBuilderStore.getState();
        const store = useCompanionModelStore.getState();
        const prev = cb.active;

        // The incoming model must stop being a companion before it is drawn as
        // the editable one, or its cells render twice.
        const incoming = store.companions[m.id];
        if (incoming) {
            if (incoming.rep !== "topology") {
                await unload_any_source(companionSourceName(incoming.name, incoming.rep)).catch(
                    () => undefined,
                );
            }
            store.remove(m.id);
        }

        await openProceduralModel(m, {collapsePanel: false});

        if (prev && prev.modelId !== m.id) {
            const detail = await viewerApi.getProceduralModel(scopeKey, prev.modelId);
            store.add({
                modelId: prev.modelId,
                name: prev.name,
                cells: Object.values(cellsFromDoc(detail.doc)),
                rep: "topology",
                // Keep the outgoing model where the incoming one had been, so
                // the two trade places instead of landing on each other.
                offsetX: incoming?.offsetX ?? 0,
                latestGlbKey: detail.latest_glb_key ?? null,
            });
        }
    };

    const removeCompanion = async (m: ProceduralModelSummary) => {
        const c = useCompanionModelStore.getState().companions[m.id];
        if (c && c.rep !== "topology") {
            await unload_any_source(companionSourceName(c.name, c.rep)).catch(() => undefined);
        }
        useCompanionModelStore.getState().remove(m.id);
    };

    /** Switch a companion between its cell topology and a compiled result. */
    const setCompanionRep = async (m: ProceduralModelSummary, rep: CompanionRep) => {
        const store = useCompanionModelStore.getState();
        const c = store.companions[m.id];
        if (!c || c.rep === rep) return;
        // Drop whatever the previous representation put in the scene, so the two
        // never stack on one model.
        if (c.rep !== "topology") {
            await unload_any_source(companionSourceName(c.name, c.rep)).catch(() => undefined);
        }
        store.setRep(m.id, rep);
        if (rep === "topology") return;
        if (!c.latestGlbKey) return;
        const sourceName = companionSourceName(c.name, rep);
        await useCellBuilderStore.getState().viewResult(c.latestGlbKey, rep === "detail" ? "detail" : "sim", sourceName);
        // Keep the compiled result where the topology was, so switching
        // representation does not also move the model.
        setSourceOffset(sourceName, {x: c.offsetX});
    };

    const toggleProceduralLoaded = async (m: ProceduralModelSummary, next: boolean) => {
        const sourceName = proceduralSourceName(m);
        try {
            if (!next) {
                // Unticking removes it from the scene and nothing else: editing
                // a model whose result is not shown is a normal thing to do
                // (edit topology, then compile), so this must not close the
                // cellbuilder behind the operator's back.
                if (useCompanionModelStore.getState().companions[m.id]) {
                    await removeCompanion(m);
                    return;
                }
                await unload_any_source(sourceName).catch(() => undefined);
                return;
            }
            // First model in: it becomes the one being EDITED — there is
            // nothing to compare it against yet, and an empty cellbuilder
            // beside a loaded model would be an odd place to land.
            // Every later one joins as a companion, so the model you are
            // working on is not swapped out from under you by a tick.
            if (!useCellBuilderStore.getState().active) {
                await openProceduralModel(m, {collapsePanel: false});
                return;
            }
            await addCompanion(m);
        } catch (e) {
            window.alert(`Failed: ${e instanceof Error ? e.message : e}`);
        }
    };

    const viewProceduralResult = async (m: ProceduralModelSummary) => {
        if (!m.latest_glb_key) return;
        try {
            await useCellBuilderStore
                .getState()
                .viewResult(m.latest_glb_key, "sim", proceduralSourceName(m));
        } catch (e) {
            window.alert(`Failed to load compiled result: ${e instanceof Error ? e.message : e}`);
        }
    };

    const createProceduralModel = async () => {
        const name = window.prompt("Name for the new procedural model:", "");
        if (!name || !name.trim()) return;
        try {
            const detail = await viewerApi.createProceduralModel(scopeKey, name.trim());
            const store = useCellBuilderStore.getState();
            store.open(detail.id, detail.name, detail.revision, detail.doc);
            void refreshProceduralModels();
        } catch (e) {
            window.alert(`Failed to create procedural model: ${e instanceof Error ? e.message : e}`);
        }
    };

    // Instantiate a new model from a template: commit the template's document
    // verbatim (so loft members / systems survive untouched — the cellbuilder's
    // box round-trip would drop them), then kick a compile and open it. The
    // committed doc's engine is mirrored onto the model, so a worker-backed
    // template auto-routes its compile to that worker.
    const createProceduralModelFromTemplate = async (tpl: ProceduralTemplate) => {
        setTemplatesOpen(false);
        setPlusOpen(false);
        const name = window.prompt("Name for the new procedural model:", tpl.name);
        if (!name || !name.trim()) return;
        try {
            const detail = await viewerApi.createProceduralModel(scopeKey, name.trim());
            const {revision} = await viewerApi.commitProceduralModel(scopeKey, detail.id, tpl.doc, detail.revision);
            // Compile so the model has a rendered GLB immediately; ignore compile
            // errors here (the model still opens and can be recompiled).
            try {
                await viewerApi.compileProceduralModel(scopeKey, detail.id);
            } catch {
                /* compile is best-effort on create */
            }
            const fresh = await viewerApi.getProceduralModel(scopeKey, detail.id);
            useCellBuilderStore
                .getState()
                .open(fresh.id, fresh.name, revision, fresh.doc);
            void refreshProceduralModels();
        } catch (e) {
            window.alert(`Failed to create from template: ${e instanceof Error ? e.message : e}`);
        }
    };

    // Rename and Move are ONE server call — the model's name is its path — but
    // they stay two menu entries because they are two different intentions, and
    // a file offers both. Rename edits the leaf and keeps the folder; Move
    // keeps the leaf and picks a folder from the ones that already exist.
    const applyProceduralName = async (m: ProceduralModelSummary, next: string) => {
        if (!next || next === m.name) return;
        try {
            await viewerApi.renameProceduralModel(scopeKey, m.id, next);
            void refreshProceduralModels();
        } catch (e) {
            // A 409 here is the scope-unique index reporting the same collision
            // a filesystem would for two entries at one path.
            window.alert(`Failed: ${e instanceof Error ? e.message : e}`);
        }
    };

    const renameProceduralModel = async (m: ProceduralModelSummary) => {
        const folder = m.name.includes("/") ? m.name.slice(0, m.name.lastIndexOf("/")) : "";
        const leaf = m.name.slice(m.name.lastIndexOf("/") + 1);
        const next = window.prompt("Rename procedural model:", leaf);
        if (next === null) return;
        const trimmed = next.trim();
        if (!trimmed) return;
        await applyProceduralName(m, folder ? `${folder}/${trimmed}` : trimmed);
    };

    const moveProceduralModel = (m: ProceduralModelSummary) => {
        const leaf = m.name.slice(m.name.lastIndexOf("/") + 1);
        setPicker({
            title: `Move "${leaf}" to folder`,
            onPick: (folder) => void applyProceduralName(m, folder ? `${folder}/${leaf}` : leaf),
        });
    };

    // Placement acts on a SCENE SOURCE, so one implementation serves both row
    // kinds: a file's source name is its key, a model's is procedural:<name>.
    const placementItems = (sourceName: string, label: string) => {
        const loaded = loadedSourceNames.has(sourceName);
        const offset = loaded ? getSourceOffset(sourceName) : {x: 0, y: 0, z: 0};
        const isOffset = loaded && (offset.x !== 0 || offset.y !== 0 || offset.z !== 0);
        return {
            isLoaded: loaded,
            isOffset,
            onPlaceNextTo: () => {
                const x = placeNextToExisting(sourceName);
                if (x === null) window.alert(`"${label}" is not in the scene.`);
            },
            onTranslate: () => {
                const cur = getSourceOffset(sourceName);
                const raw = window.prompt(
                    `Offset for "${label}" from the shared origin, as "x, y, z" in metres:`,
                    `${cur.x}, ${cur.y}, ${cur.z}`,
                );
                if (raw === null) return;
                const parts = raw.split(",").map((v) => Number(v.trim()));
                if (parts.length !== 3 || parts.some((v) => !Number.isFinite(v))) {
                    window.alert('Expected three numbers, e.g. "25, 0, 0".');
                    return;
                }
                setSourceOffset(sourceName, {x: parts[0], y: parts[1], z: parts[2]});
            },
            onResetPlacement: () => resetSourceOffset(sourceName),
        };
    };

    const proceduralMenuItems = (m: ProceduralModelSummary, displayName: string): KebabMenuItem[] =>
        buildProceduralMenuItems(displayName, {
            canMutate,
            onOpen: () => void openProceduralModel(m),
            ...placementItems(proceduralSourceName(m), m.name),
            isActive: activeProcedural === m.id,
            companionRep: companions[m.id]?.rep ?? null,
            hasCompiled: !!m.latest_glb_key,
            onShowRep: (rep) => void setCompanionRep(m, rep),
            onMakeActive: () => void makeProceduralActive(m),
            onDeactivate: () => useCellBuilderStore.getState().close(),
            onViewResult: m.latest_glb_key ? () => void viewProceduralResult(m) : undefined,
            onCopyPath: () => void writeToClipboard(m.name),
            onRename: () => void renameProceduralModel(m),
            onMoveToFolder: () => moveProceduralModel(m),
            onDelete: () => void deleteProceduralModel(m),
        });

    const deleteProceduralModel = async (m: ProceduralModelSummary) => {
        if (!window.confirm(`Delete procedural model "${m.name}"?`)) return;
        try {
            await viewerApi.deleteProceduralModel(scopeKey, m.id);
            const st = useCellBuilderStore.getState();
            if (st.active?.modelId === m.id) st.close();
            void refreshProceduralModels();
        } catch (e) {
            window.alert(`Failed to delete: ${e instanceof Error ? e.message : e}`);
        }
    };

    const proceduralByName = useMemo(
        () => new Map(proceduralModels.map((m) => [m.name, m])),
        [proceduralModels],
    );

    return {
        proceduralModels,
        proceduralByName,
        activeProcedural,
        importPrompt,
        importEngines,
        refreshProceduralModels,
        allTemplates,
        templatesOpen,
        setTemplatesOpen,
        templatesBtnRef,
        openProceduralModel,
        proceduralSourceName,
        isProceduralLoaded,
        companions,
        toggleProceduralLoaded,
        createProceduralModel,
        createProceduralModelFromTemplate,
        applyProceduralName,
        placementItems,
        proceduralMenuItems,
        deleteProceduralModel,
    };
}
