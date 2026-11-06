// The FEA result data table in a pop-out window: `core:fea-table`.
//
// The table lists the nodes of the shown step (tens of thousands of rows), so a
// pop-out is not sent the step: it sends its QUERY (the node-id filter and the sort)
// to the main window, which reads the step, applies the query (`./feaTableRows`, the
// code the table itself uses) and publishes the first `FEA_TABLE_ROW_CAP` rows of the
// answer with the count it matched. The pop-out shows those rows; "showing the first
// N of M" tells the user to narrow the filter or sort to reach the others.
//
// The main window reads a step only while a pop-out WATCHES (its table is mounted):
// `core:fea-table-watch` says so, and a window that closed without saying so is
// dropped at the next refresh.
//
// Commands: `core:fea-table-watch` {on}, `core:fea-table-query` {filter, sort},
// `core:fea-goto-node` {id | null} (the eye on a row: mark the node in the 3D view).
//
// Light on purpose: the step reader and the scene code are imported when needed.

import type {MirrorTopic} from "@/layout/windowMirror";
import {registerMirrorCommand, registerMirrorTopic} from "@/layout/windowMirror";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {useTableNavStore} from "@/state/tableNavStore";

import {isNodalField, loadFeaStepValues, tableRowIndices, type TableSort} from "./feaTableRows";

export const FEA_TABLE_TOPIC = "core:fea-table";
export const FEA_TABLE_WATCH_COMMAND = "core:fea-table-watch";
export const FEA_TABLE_QUERY_COMMAND = "core:fea-table-query";
export const FEA_GOTO_NODE_COMMAND = "core:fea-goto-node";

/** Most rows one snapshot carries. */
export const FEA_TABLE_ROW_CAP = 2000;

export interface FeaTableQuery {
    filter: string;
    sort: TableSort | null;
}

export interface FeaTableSnapshot {
    /** `idle`: nothing to list (no result, or an element field). */
    state: "idle" | "loading" | "ready" | "error";
    error: string | null;
    /** What the rows are of: source, field and step. A snapshot of another key is not the shown step's. */
    key: string | null;
    nComponents: number;
    nPoints: number;
    /** Rows the query matched; `ids` holds the first `FEA_TABLE_ROW_CAP` of them. */
    matched: number;
    query: FeaTableQuery;
    /** 1-based node ids of the rows sent. */
    ids: number[];
    /** The rows' values, `nComponents` each, in `ids` order. */
    values: Float32Array;
    /** The node marked in the 3D view. */
    activeNodeId: number | null;
}

const EMPTY_VALUES = new Float32Array(0);

/** The state of the main window's table reader. */
interface Reader {
    watchers: Set<string>;
    query: FeaTableQuery;
    cache: {key: string; values: Float32Array} | null;
    loading: string | null;
    error: {key: string; message: string} | null;
}

let reader: Reader = freshReader();
/** Reads a step; replaced in tests. */
let stepLoader: typeof loadFeaStepValues = loadFeaStepValues;
export function setFeaTableStepLoaderForTests(fn: typeof loadFeaStepValues | null): void {
    stepLoader = fn ?? loadFeaStepValues;
}
const listeners = new Set<() => void>();
/** Window ids that are open, for dropping watchers that went away. */
let liveWindows: () => readonly string[] = () => [];

function freshReader(): Reader {
    return {watchers: new Set(), query: {filter: "", sort: null}, cache: null, loading: null, error: null};
}

const notify = () => {
    for (const l of [...listeners]) l();
};

/** The shown nodal field and the key of its shown step, or null when the table lists nothing. */
function shown(): {key: string; sourceName: string; stepIndex: number; field: NonNullable<ReturnType<typeof fieldOf>>} | null {
    const s = useFeaAnimationStore.getState();
    if (!s.sessionActive || !s.sourceName) return null;
    const field = fieldOf(s.manifest, s.fieldName);
    if (!isNodalField(field)) return null;
    return {key: `${s.sourceName}\u0000${field.name_canonical}\u0000${s.stepIndex}`, sourceName: s.sourceName, stepIndex: s.stepIndex, field};
}

function fieldOf(manifest: ReturnType<typeof useFeaAnimationStore.getState>["manifest"], name: string | null) {
    return manifest && name ? (manifest.fields.find((f) => f.name_canonical === name) ?? null) : null;
}

function prune(): void {
    if (!reader.watchers.size) return;
    const live = new Set(liveWindows());
    for (const id of [...reader.watchers]) if (!live.has(id)) reader.watchers.delete(id);
}

/** Read the shown step when a window watches and it is not read yet. */
export function refreshFeaTable(): void {
    prune();
    if (!reader.watchers.size) return;
    const want = shown();
    if (!want || reader.cache?.key === want.key || reader.loading === want.key) return;
    if (reader.error?.key === want.key) return;
    const key = want.key;
    reader.loading = key;
    notify();
    const scope = scopeUrlPart(useScopeStore.getState().current);
    stepLoader(scope, want.sourceName, want.field, want.stepIndex).then(
        (values) => {
            if (reader.loading === key) reader.loading = null;
            reader.cache = {key, values};
            reader.error = null;
            // Another step may have been asked for meanwhile.
            notify();
            refreshFeaTable();
        },
        (err) => {
            if (reader.loading === key) reader.loading = null;
            reader.error = {key, message: String((err as Error)?.message ?? err)};
            notify();
        },
    );
}

let memo: {values: Float32Array; query: FeaTableQuery; nComponents: number; out: {ids: number[]; vals: Float32Array; matched: number}} | null = null;

/** The snapshot of the reader now. */
export function feaTableSnapshot(): FeaTableSnapshot {
    const query = reader.query;
    const activeNodeId = useTableNavStore.getState().activeNodeId;
    const base = {key: null, nComponents: 0, nPoints: 0, matched: 0, query, ids: [] as number[], values: EMPTY_VALUES, activeNodeId, error: null};
    const want = shown();
    if (!want) return {...base, state: "idle"};
    const common = {...base, key: want.key};
    if (reader.error?.key === want.key) return {...common, state: "error", error: reader.error.message};
    const cached = reader.cache;
    if (!cached || cached.key !== want.key) return {...common, state: "loading"};
    const nComponents = want.field.components.length;
    const isVector = want.field.kind.startsWith("vector");
    const nPoints = nComponents > 0 ? Math.floor(cached.values.length / nComponents) : 0;
    if (!memo || memo.values !== cached.values || memo.query !== query || memo.nComponents !== nComponents) {
        const rows = tableRowIndices(nPoints, query.filter, query.sort, cached.values, nComponents, isVector);
        const n = Math.min(rows.length, FEA_TABLE_ROW_CAP);
        const ids = new Array<number>(n);
        const vals = new Float32Array(n * nComponents);
        for (let k = 0; k < n; k++) {
            ids[k] = rows[k] + 1;
            vals.set(cached.values.subarray(rows[k] * nComponents, (rows[k] + 1) * nComponents), k * nComponents);
        }
        memo = {values: cached.values, query, nComponents, out: {ids, vals, matched: rows.length}};
    }
    const {ids, vals, matched} = memo.out;
    return {...common, state: "ready", nComponents, nPoints, matched, ids, values: vals};
}

/** The pop-out's copy of the snapshot (read by the mirrored table). */
let received: FeaTableSnapshot | null = null;
const receivedListeners = new Set<() => void>();

export function receivedFeaTable(): FeaTableSnapshot | null {
    return received;
}

export function subscribeReceivedFeaTable(cb: () => void): () => void {
    receivedListeners.add(cb);
    return () => void receivedListeners.delete(cb);
}

/** Apply a snapshot (a pop-out): the table state and the marked node. */
export function applyFeaTable(data: FeaTableSnapshot): boolean {
    if (!data || typeof data !== "object") return false;
    received = data;
    useTableNavStore.setState({activeNodeId: data.activeNodeId ?? null});
    for (const l of [...receivedListeners]) l();
    return true;
}

export function feaTableTopic(): MirrorTopic<FeaTableSnapshot> {
    return {
        id: FEA_TABLE_TOPIC,
        snapshot: feaTableSnapshot,
        apply: applyFeaTable,
        subscribe(cb) {
            listeners.add(cb);
            const offFea = useFeaAnimationStore.subscribe((s, p) => {
                if (s.stepIndex === p.stepIndex && s.fieldName === p.fieldName && s.sourceName === p.sourceName && s.manifest === p.manifest && s.sessionActive === p.sessionActive) return;
                refreshFeaTable();
                cb();
            });
            const offNav = useTableNavStore.subscribe((s, p) => void (s.activeNodeId !== p.activeNodeId && cb()));
            return () => {
                listeners.delete(cb);
                offFea();
                offNav();
            };
        },
    };
}

/** Clamp a query a window sent. */
export function checkedQuery(raw: unknown): FeaTableQuery | null {
    const v = raw as {filter?: unknown; sort?: unknown} | null;
    if (!v || typeof v !== "object" || typeof v.filter !== "string") return null;
    let sort: TableSort | null = null;
    if (v.sort !== null && v.sort !== undefined) {
        const s = v.sort as {col?: unknown; dir?: unknown};
        if (typeof s.col !== "number" || !Number.isInteger(s.col) || s.col < 0 || s.col > 1000 || (s.dir !== "asc" && s.dir !== "desc")) return null;
        sort = {col: s.col, dir: s.dir};
    }
    return {filter: v.filter.slice(0, 40), sort};
}

let registered = false;

/** Register the topic and the commands (once per page). `windows` lists the open windows' ids. */
export function installFeaTable(windows?: () => readonly string[]): void {
    if (registered) return;
    registered = true;
    if (windows) liveWindows = windows;
    registerMirrorTopic(feaTableTopic());
    registerMirrorCommand<{on?: unknown}>({
        id: FEA_TABLE_WATCH_COMMAND,
        run: (args, from) => {
            if (args?.on === true) reader.watchers.add(from);
            else reader.watchers.delete(from);
            refreshFeaTable();
            notify();
        },
    });
    registerMirrorCommand({
        id: FEA_TABLE_QUERY_COMMAND,
        run: (args) => {
            const q = checkedQuery(args);
            if (!q) return;
            reader.query = q;
            notify();
        },
    });
    registerMirrorCommand<{id?: unknown}>({
        id: FEA_GOTO_NODE_COMMAND,
        run: (args) => {
            const id = args?.id;
            if (id === null) {
                void import("./goToNode").then((m) => m.clearGoToNode());
                useTableNavStore.getState().setActiveNodeId(null);
            } else if (typeof id === "number" && Number.isInteger(id) && id > 0) {
                void import("./goToNode").then((m) => m.goToNode(id));
                useTableNavStore.getState().setActiveNodeId(id);
            }
        },
    });
}

/** Forget the registration and the state -- tests only (with `resetMirror`). */
export function resetFeaTableForTests(): void {
    registered = false;
    reader = freshReader();
    liveWindows = () => [];
    stepLoader = loadFeaStepValues;
    memo = null;
    received = null;
}
