import type {ServerFileEntry} from "@/state/serverInfoStore";
import {viewerApi, type ProceduralModelSummary} from "@/services/viewerApi";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {previewKeyList} from "@/utils/storage/fileTree";
import {request_list_of_files_from_server} from "@/utils/server_info/handlers/request_list_of_files_from_server";
import {unload_any_source} from "@/utils/scene/handlers/unload_any_source";
import {unload_source_from_scene} from "@/utils/scene/handlers/unload_source_from_scene";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {canLoadIntoSceneLegacy, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import type {useStorageMutations} from "../useStorageMutations";
import type {FolderPicker} from "./useUploads";

export type BulkBusy = "load" | "unload" | "clear" | "delete" | null;

// Load / unload of single rows and of the whole selection, bulk delete /
// move, and the scene-wide Clear. Files and procedural models live in one
// selection set but are different things on the server, so every bulk action
// fans out accordingly.
export function useBulkActions(p: {
    files: ServerFileEntry[];
    scopeKey: string;
    selection: Set<string>;
    clearSelection: () => void;
    loadedSourceNames: ReadonlySet<string>;
    enqueueLoad: (t: {name: string; streamer?: boolean}) => void;
    removeQueuedLoad: (name: string) => void;
    queuedLoadNames: Set<string>;
    viewingName: string | null;
    proceduralByName: Map<string, ProceduralModelSummary>;
    isProceduralLoaded: (m: ProceduralModelSummary) => boolean;
    toggleProceduralLoaded: (m: ProceduralModelSummary, next: boolean) => Promise<void>;
    refreshProceduralModels: () => Promise<void>;
    applyProceduralName: (m: ProceduralModelSummary, next: string) => Promise<void>;
    mutations: ReturnType<typeof useStorageMutations>;
    unloadIfLoaded: (name: string) => Promise<void>;
    moveKeysWithProgress: (keys: string[], folder: string) => Promise<void>;
    alertError: (e: unknown) => void;
    setPicker: (picker: FolderPicker | null) => void;
    bulkBusy: BulkBusy;
    setBulkBusy: (b: BulkBusy) => void;
}) {
    const {
        files, scopeKey, selection, clearSelection, loadedSourceNames, enqueueLoad, removeQueuedLoad,
        queuedLoadNames, viewingName, proceduralByName, isProceduralLoaded, toggleProceduralLoaded,
        refreshProceduralModels, applyProceduralName, mutations, unloadIfLoaded, moveKeysWithProgress,
        alertError, setPicker, bulkBusy, setBulkBusy,
    } = p;

    // Toggle a file in/out of the scene. All adds go through the
    // overlay path so multiple models can coexist; ``Clear`` in
    // the header drops everything if you want a fresh view. The
    // first checked file behaves identically to a normal load
    // (the loader's else branch computes a translation from its
    // bbox); subsequent files reuse that translation so they
    // overlay correctly.
    const onToggle = async (entry: ServerFileEntry, nextChecked: boolean) => {
        if (nextChecked) {
            // Queue the load — more can be queued while one is in
            // flight; the queue drains sequentially (shared loader
            // state can't take concurrent loads).
            enqueueLoad({name: entry.name});
            return;
        }
        if (queuedLoadNames.has(entry.name)) {
            removeQueuedLoad(entry.name);
            return;
        }
        if (viewingName === entry.name) return; // mid-load; can't cancel
        try {
            await unload_any_source(entry.name);
        } catch (err) {
            console.error("storage toggle failed", err);
        }
    };

    // Load a STEP file via the memory-bounded streaming converter (one solid at a
    // time) — for large assemblies whose normal OCC->GLB conversion OOM-kills the
    // worker. Same overlay flow as onToggle, with the streamer flag set.
    const onLoadStreamer = (name: string) => {
        enqueueLoad({name, streamer: true});
    };

    // Bulk "show all" — overlay every file currently absent from the
    // scene. Sequential (not parallel) because overlay_file_in_scene
    // shares loader state and races corrupt the scene; the per-row
    // viewingName indicator follows along so the user sees progress.
    // Apply load/unload to the multi-selection set. Sequential
    // because overlay_file_in_scene shares loader state and races
    // would corrupt the scene; we do want to load even
    // already-loaded items (no-op overlay) and unload already-hidden
    // items (no-op unload) so the user gets a predictable result
    // regardless of the per-row state mix.
    const onLoadSelected = () => {
        const targets = files.filter((f) =>
            selection.has(f.name) && !loadedSourceNames.has(f.name) &&
            (isStreamingFEAResult(f.name) || canLoadIntoSceneLegacy(f.name)));
        for (const f of targets) enqueueLoad({name: f.name});
        // Models load through their own path (a compiled result, not a source
        // blob), but the button means the same thing, so a mixed selection
        // loads both. The last one ticked ends up active, which matches what
        // ticking a single row does.
        const {models} = splitSelection(Array.from(selection));
        void (async () => {
            for (const m of models) {
                if (m.latest_glb_key && !isProceduralLoaded(m)) {
                    await toggleProceduralLoaded(m, true);
                }
            }
        })();
        clearSelection();
    };
    const onUnloadSelected = () => {
        if (bulkBusy !== null) return;
        const {models} = splitSelection(Array.from(selection));
        void (async () => {
            for (const m of models) {
                if (isProceduralLoaded(m)) await toggleProceduralLoaded(m, false);
            }
        })();
        const targets = files.filter((f) => selection.has(f.name) && loadedSourceNames.has(f.name));
        setBulkBusy("unload");
        try {
            for (const f of targets) {
                try {
                    unload_source_from_scene(f.name);
                } catch (err) {
                    console.error("unload-selected failed", f.name, err);
                }
            }
        } finally {
            setBulkBusy(null);
            clearSelection();
        }
    };

    /** Split a selection into procedural models and real storage keys.
     *
     * Both live in one tree and one selection set, but they are different
     * things on the server: a model is a row addressed by UUID, a file is a
     * blob addressed by key. Every bulk action has to fan out accordingly —
     * deleting a model through the storage API would 404, and moving one would
     * silently do nothing. */
    const splitSelection = (keys: string[]) => {
        const models: ProceduralModelSummary[] = [];
        const fileKeys: string[] = [];
        for (const k of keys) {
            const m = proceduralByName.get(k);
            if (m) models.push(m);
            else fileKeys.push(k);
        }
        return {models, fileKeys};
    };

    // Bulk delete / move over the selection set. Version blobs are
    // server-protected (400), so the toolbar disables these when the
    // selection includes any — no silent skipping.
    const onDeleteSelected = async () => {
        if (bulkBusy !== null) return;
        const keys = Array.from(selection);
        if (keys.length === 0) return;
        const {models, fileKeys} = splitSelection(keys);
        // Name both kinds. "Delete 5 files" when two of them are models would
        // be a prompt that misdescribes what it is about to do.
        const what = [
            fileKeys.length ? `${fileKeys.length} file${fileKeys.length === 1 ? "" : "s"}` : "",
            models.length ? `${models.length} procedural model${models.length === 1 ? "" : "s"}` : "",
        ].filter(Boolean).join(" and ");
        if (!window.confirm(
            `Delete ${what}?\n` +
            "Converted view caches are removed too.\n\n" +
            previewKeyList(keys),
        )) return;
        setBulkBusy("delete");
        try {
            // Sequential: deletes cascade derived blobs server-side and
            // parallel calls would race on the storage listing.
            for (const k of fileKeys) {
                await unloadIfLoaded(k);
                await mutations.deleteKey(k);
            }
            // Models are rows, not blobs — a different endpoint entirely.
            for (const m of models) {
                await viewerApi.deleteProceduralModel(scopeKey, m.id);
                const st = useCellBuilderStore.getState();
                if (st.active?.modelId === m.id) st.close();
            }
            if (models.length) void refreshProceduralModels();
            void request_list_of_files_from_server();
        } catch (e) {
            alertError(e);
        } finally {
            setBulkBusy(null);
            clearSelection();
        }
    };
    const onMoveSelected = () => {
        const keys = Array.from(selection);
        if (keys.length === 0) return;
        const {models, fileKeys} = splitSelection(keys);
        setPicker({
            title: `Move ${keys.length} item${keys.length === 1 ? "" : "s"} to folder`,
            onPick: async (folder) => {
                // Models move by rename — their name IS their path — so a mixed
                // selection fans out to two APIs and lands in one folder.
                for (const m of models) {
                    const leaf = m.name.slice(m.name.lastIndexOf("/") + 1);
                    await applyProceduralName(m, folder ? `${folder}/${leaf}` : leaf);
                }
                if (fileKeys.length) await moveKeysWithProgress(fileKeys, folder);
                else if (models.length) clearSelection();
            },
        });
    };

    // Drop every loaded source via the canonical teardown.
    // clear_loaded_model resets animation state, tree-view,
    // model-key map, scene groups, and selection in one shot;
    // iterating unload_source_from_scene per file would leave that
    // bookkeeping stale.
    const onHideAll = async () => {
        if (bulkBusy !== null) return;
        setBulkBusy("clear");
        try {
            await clear_loaded_model();
            // Also close any open procedural model — its cellbuilder proxies /
            // compiled result are part of "what's in the scene", so Clear should
            // tear that down too (and hide the cellbuilder panel).
            const cb = useCellBuilderStore.getState();
            if (cb.active) cb.close();
        } catch (err) {
            console.error("clear scene failed", err);
        } finally {
            setBulkBusy(null);
        }
    };

    return {
        onToggle,
        onLoadStreamer,
        onLoadSelected,
        onUnloadSelected,
        splitSelection,
        onDeleteSelected,
        onMoveSelected,
        onHideAll,
    };
}
