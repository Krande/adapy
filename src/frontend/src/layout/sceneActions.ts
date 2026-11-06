// What a scene panel DOES, behind one seam, so its code is one path in every
// window (`useSceneActions`):
//
//   * in the main window (the one with the 3D view) each action runs here, on
//     this window's scene;
//   * in a pop-out window (`isMirroredWindow`) it is sent to the main window as a
//     mirror command (`./windowMirror`), which runs the same action there and
//     publishes the outcome back (`./windowMirrorCore`'s topics).
//
// The actions that are core commands (`CORE_COMMAND_IDS`: hide / hide others /
// show all, clear selection, fit, undo / redo) run that command's action
// (`runCoreCommandAction`) under one mirror command, `core:run-command`, limited
// to `MIRRORED_CORE_COMMANDS`. The others have mirror commands of their own
// (`SCENE_ACTION_COMMANDS`), taking row ids, never objects.
//
// The scene code an action runs is imported when it runs.

import {useMemo} from "react";

import type {TreeNodeData} from "@/components/tree_view/CustomNode";

import {CORE_COMMAND_IDS} from "./coreCommandIds";
import {MAIN_WINDOW_ID, type WindowBus} from "./windowBus";
import {registerMirrorCommand, sendMirrorCommand} from "./windowMirror";
import {thisWindowId, windowBus} from "./windows";

/** How a selection of rows combines with the one there is. */
export type RowSelectMode = "replace" | "add" | "toggle";

export interface SceneActions {
    /** Hide the selection (Shift+H). */
    hideSelection(): void;
    /** Hide everything that is not selected (Shift+I). */
    hideOthers(): void;
    /** Unhide every range, and every procedural cell (Shift+U). */
    showAll(): void;
    fitAll(): void;
    fitSelection(): void;
    clearSelection(): void;
    undo(): void;
    redo(): void;
    /** Select the parent level of the selection (the object info's up arrow). */
    selectParent(): void;
    /** Select a tree node by id (its whole subtree). */
    selectNode(id: string): void;
    /** Select these rows (each one's subtree), as a click in the scene tree does. */
    selectRows(rows: readonly TreeNodeData[], mode?: RowSelectMode): void;
    hideRows(rows: readonly TreeNodeData[]): void;
    showRows(rows: readonly TreeNodeData[]): void;
    /** Show these rows and hide everything else. */
    isolateRows(rows: readonly TreeNodeData[]): void;
    /** Select these rows and frame them. */
    fitRows(rows: readonly TreeNodeData[]): void;
    /** Unload every model (the tree's "Remove all"). */
    clearScene(): Promise<void>;
    /** Isolate the loaded FEA result's rows `ids` (element sets, property values): replace the
     *  isolation, add to it, or take them out of it. */
    feaIsolate(ids: readonly string[], mode?: "replace" | "add" | "remove"): void;
    /** Isolate the element sets whose name holds `text`. */
    feaIsolateMatching(text: string): void;
    /** Show the whole FEA model again. */
    feaClearIsolation(): void;
    /** Draw what the FEA isolation hides as a wireframe, or not. */
    feaToggleContext(): void;
    /** Hide (show) the elements of these FEA rows. */
    feaHideRows(ids: readonly string[]): void;
    feaShowRows(ids: readonly string[]): void;
}

/** The core commands a pop-out may run in the main window (`core:run-command`). */
export const MIRRORED_CORE_COMMANDS: readonly string[] = [
    CORE_COMMAND_IDS.hideSelected,
    CORE_COMMAND_IDS.hideOthers,
    CORE_COMMAND_IDS.showAll,
    CORE_COMMAND_IDS.fitAll,
    CORE_COMMAND_IDS.fitSelection,
    CORE_COMMAND_IDS.clearSelection,
    CORE_COMMAND_IDS.undo,
    CORE_COMMAND_IDS.redo,
];

/** The mirror commands behind the actions. */
export const SCENE_ACTION_COMMANDS = {
    runCommand: "core:run-command",
    selectNode: "core:select-node",
    selectParent: "core:select-parent",
    selectRows: "core:select-rows",
    hideRows: "core:hide-rows",
    showRows: "core:show-rows",
    isolateRows: "core:isolate-rows",
    fitRows: "core:fit-rows",
    clearScene: "core:clear-scene",
    feaIsolate: "core:fea-isolate",
    feaIsolateMatching: "core:fea-isolate-matching",
    feaClearIsolation: "core:fea-clear",
    feaToggleContext: "core:fea-context",
    feaHideRows: "core:fea-hide-rows",
    feaShowRows: "core:fea-show-rows",
} as const;

/** Is this page a window without the scene, whose scene panels act through the main window. */
export function isMirroredWindow(): boolean {
    return thisWindowId() !== MAIN_WINDOW_ID;
}

const coreRun = (id: string) => void import("./coreCommands").then((m) => m.runCoreCommandAction(id));
const treeActions = () => import("@/utils/tree_view/sceneTreeActions");
const treeSelect = () => import("@/utils/tree_view/treeSelectionActions");
const feaActions = () => import("@/utils/scene/fea/feaIsolation");

/** The actions, run on this window's scene; and a selection of rows made in another window (shown in this one's tree too). */
export const localSceneActions: SceneActions & {selectRowsFromElsewhere(rows: readonly TreeNodeData[], mode: RowSelectMode): void} = {
    hideSelection: () => coreRun(CORE_COMMAND_IDS.hideSelected),
    hideOthers: () => coreRun(CORE_COMMAND_IDS.hideOthers),
    showAll: () => {
        coreRun(CORE_COMMAND_IDS.showAll);
        // "Show everything" never leaves something hidden in the other system.
        void import("@/state/cellBuilderStore").then(({useCellBuilderStore}) => {
            const cb = useCellBuilderStore.getState();
            if (cb.active !== null) cb.unhideAllCells();
        });
    },
    fitAll: () => coreRun(CORE_COMMAND_IDS.fitAll),
    fitSelection: () => coreRun(CORE_COMMAND_IDS.fitSelection),
    clearSelection: () => coreRun(CORE_COMMAND_IDS.clearSelection),
    undo: () => coreRun(CORE_COMMAND_IDS.undo),
    redo: () => coreRun(CORE_COMMAND_IDS.redo),
    selectParent: () => void import("@/utils/tree_view/treeNavigation").then((m) => m.selectParentLevel()),
    selectNode: (id) => void treeSelect().then((m) => m.selectNodeById(id)),
    selectRows: (rows, mode = "replace") => void treeSelect().then((m) => m.selectRows(rows, mode)),
    selectRowsFromElsewhere: (rows, mode) => void treeSelect().then((m) => m.selectRows(rows, mode, {syncTree: true})),
    hideRows: (rows) => void treeActions().then((m) => m.hideRows(rows)),
    showRows: (rows) => void treeActions().then((m) => m.showRows(rows)),
    isolateRows: (rows) => void treeActions().then((m) => m.isolateRows(rows)),
    fitRows: (rows) => void treeSelect().then((m) => m.fitRows(rows)),
    clearScene: () => import("@/utils/scene/handlers/clear_scene").then((m) => m.clearScene()),
    feaIsolate: (ids, mode = "replace") => void feaActions().then((m) => m.isolateFeaRows(ids, mode)),
    feaIsolateMatching: (text) => void feaActions().then((m) => m.isolateFeaSetsMatching(text)),
    feaClearIsolation: () => void feaActions().then((m) => m.clearFeaIsolation()),
    feaToggleContext: () => void feaActions().then((m) => m.toggleFeaContextWireframe()),
    feaHideRows: (ids) => void feaActions().then((m) => m.hideFeaRows(ids)),
    feaShowRows: (ids) => void feaActions().then((m) => m.showFeaRows(ids)),
};

const ids = (rows: readonly TreeNodeData[]) => rows.map((r) => String(r.id));

/** The actions, sent to the main window over `bus`. */
export function remoteSceneActions(bus: WindowBus = windowBus()): SceneActions {
    const send = (name: string, args: unknown = null) => sendMirrorCommand(bus, name, args);
    const run = (id: string) => () => send(SCENE_ACTION_COMMANDS.runCommand, {id});
    return {
        hideSelection: run(CORE_COMMAND_IDS.hideSelected),
        hideOthers: run(CORE_COMMAND_IDS.hideOthers),
        showAll: () => send(SCENE_ACTION_COMMANDS.runCommand, {id: CORE_COMMAND_IDS.showAll}),
        fitAll: run(CORE_COMMAND_IDS.fitAll),
        fitSelection: run(CORE_COMMAND_IDS.fitSelection),
        clearSelection: run(CORE_COMMAND_IDS.clearSelection),
        undo: run(CORE_COMMAND_IDS.undo),
        redo: run(CORE_COMMAND_IDS.redo),
        selectParent: () => send(SCENE_ACTION_COMMANDS.selectParent),
        selectNode: (id) => send(SCENE_ACTION_COMMANDS.selectNode, {id}),
        selectRows: (rows, mode = "replace") => send(SCENE_ACTION_COMMANDS.selectRows, {ids: ids(rows), mode}),
        hideRows: (rows) => send(SCENE_ACTION_COMMANDS.hideRows, {ids: ids(rows)}),
        showRows: (rows) => send(SCENE_ACTION_COMMANDS.showRows, {ids: ids(rows)}),
        isolateRows: (rows) => send(SCENE_ACTION_COMMANDS.isolateRows, {ids: ids(rows)}),
        fitRows: (rows) => send(SCENE_ACTION_COMMANDS.fitRows, {ids: ids(rows)}),
        clearScene: async () => send(SCENE_ACTION_COMMANDS.clearScene),
        feaIsolate: (rowIds, mode = "replace") => send(SCENE_ACTION_COMMANDS.feaIsolate, {ids: [...rowIds], mode}),
        feaIsolateMatching: (text) => send(SCENE_ACTION_COMMANDS.feaIsolateMatching, {text}),
        feaClearIsolation: () => send(SCENE_ACTION_COMMANDS.feaClearIsolation),
        feaToggleContext: () => send(SCENE_ACTION_COMMANDS.feaToggleContext),
        feaHideRows: (rowIds) => send(SCENE_ACTION_COMMANDS.feaHideRows, {ids: [...rowIds]}),
        feaShowRows: (rowIds) => send(SCENE_ACTION_COMMANDS.feaShowRows, {ids: [...rowIds]}),
    };
}

/** This window's actions: run here in the main window, sent there from a pop-out. */
export function sceneActions(): SceneActions {
    return isMirroredWindow() ? remoteSceneActions() : localSceneActions;
}

/** `sceneActions()` for a component. */
export function useSceneActions(): SceneActions {
    return useMemo(sceneActions, []);
}

/** String ids from a command's `args.ids`, at most `limit` of them; [] for anything else. */
export function argIds(args: unknown, limit = 1_000_000): string[] {
    const raw = (args as {ids?: unknown} | null)?.ids;
    if (!Array.isArray(raw)) return [];
    return raw.filter((x): x is string => typeof x === "string").slice(0, limit);
}

let registered = false;

/**
 * Let pop-out windows run the actions here (once per page). `rowsById` resolves
 * the ids a command carries to this window's tree rows (unknown ids dropped).
 */
export function registerSceneActionCommands(rowsById: (ids: readonly string[]) => TreeNodeData[]): void {
    if (registered) return;
    registered = true;
    // Read at each run, not bound now: one action object, whatever replaced a method of it.
    const a = localSceneActions;
    const C = SCENE_ACTION_COMMANDS;
    const byCoreCommand: Record<string, keyof SceneActions> = {
        [CORE_COMMAND_IDS.hideSelected]: "hideSelection",
        [CORE_COMMAND_IDS.hideOthers]: "hideOthers",
        [CORE_COMMAND_IDS.showAll]: "showAll",
        [CORE_COMMAND_IDS.fitAll]: "fitAll",
        [CORE_COMMAND_IDS.fitSelection]: "fitSelection",
        [CORE_COMMAND_IDS.clearSelection]: "clearSelection",
        [CORE_COMMAND_IDS.undo]: "undo",
        [CORE_COMMAND_IDS.redo]: "redo",
    };
    registerMirrorCommand<{id: string}>({
        id: C.runCommand,
        run: (args) => {
            const id = args?.id;
            if (typeof id !== "string" || !MIRRORED_CORE_COMMANDS.includes(id)) return;
            (a[byCoreCommand[id]] as () => void)();
        },
    });
    registerMirrorCommand<{id: string}>({
        id: C.selectNode,
        run: (args) => {
            if (typeof args?.id === "string") a.selectNode(args.id);
        },
    });
    registerMirrorCommand({id: C.selectParent, run: () => a.selectParent()});
    registerMirrorCommand<{ids: string[]; mode?: RowSelectMode}>({
        id: C.selectRows,
        run: (args) => {
            const mode = args?.mode === "add" || args?.mode === "toggle" ? args.mode : "replace";
            const rows = rowsById(argIds(args));
            // Shown in this window's tree as well: the selection was made elsewhere.
            a.selectRowsFromElsewhere(rows, mode);
        },
    });
    const rowsCommand = (id: string, action: "hideRows" | "showRows" | "isolateRows" | "fitRows") =>
        registerMirrorCommand<{ids: string[]}>({
            id,
            run: (args) => {
                const rows = rowsById(argIds(args));
                if (rows.length) a[action](rows);
            },
        });
    rowsCommand(C.hideRows, "hideRows");
    rowsCommand(C.showRows, "showRows");
    rowsCommand(C.isolateRows, "isolateRows");
    rowsCommand(C.fitRows, "fitRows");
    registerMirrorCommand({id: C.clearScene, run: () => void a.clearScene().catch((err) => console.error("remove all failed", err))});
    // The FEA rows' actions: ids of FEA rows (not of a model's tree), which the action checks.
    registerMirrorCommand<{ids: string[]; mode?: string}>({
        id: C.feaIsolate,
        run: (args) => {
            const mode = args?.mode === "add" || args?.mode === "remove" ? args.mode : "replace";
            a.feaIsolate(argIds(args), mode);
        },
    });
    registerMirrorCommand<{text: string}>({
        id: C.feaIsolateMatching,
        run: (args) => {
            if (typeof args?.text === "string") a.feaIsolateMatching(args.text.slice(0, 500));
        },
    });
    registerMirrorCommand({id: C.feaClearIsolation, run: () => a.feaClearIsolation()});
    registerMirrorCommand({id: C.feaToggleContext, run: () => a.feaToggleContext()});
    registerMirrorCommand<{ids: string[]}>({id: C.feaHideRows, run: (args) => a.feaHideRows(argIds(args))});
    registerMirrorCommand<{ids: string[]}>({id: C.feaShowRows, run: (args) => a.feaShowRows(argIds(args))});
}

/** Forget the registration -- tests only (with `resetMirror`). */
export function resetSceneActionCommands(): void {
    registered = false;
}
