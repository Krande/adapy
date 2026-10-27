import {create} from "zustand";

import type {WireGeometryPlan} from "@/services/api/clashCheck";
import type {ScopeUrl} from "@/services/api/client";
import type {BoxSelectMode} from "@/utils/bboxSelection/boxMath";
import {
    boxPlanTarget,
    boxRequestChoices,
    boxRequestSentence,
    noChoiceReason,
    summariseBoxPlan,
    type RequestProviderChoice,
} from "@/utils/bboxSelection/boxRequest";
import type {GroupMember} from "@/utils/groups/savedGroups";

// "Request from source" on a selection box (see `utils/bboxSelection/boxRequest.ts`): one request
// per box at a time, kept here so it outlives the box's menu -- the Scene panel's box list shows
// where it has got to. The provider jobs themselves are in the global job toast.

export type BoxRequestPhase = "loading" | "ready" | "planning" | "confirm" | "requesting" | "done" | "error";

export interface BoxRequestEntry {
    readonly phase: BoxRequestPhase;
    /** Partially or fully within: which elements the request takes. */
    readonly mode: BoxSelectMode;
    readonly choices: readonly RequestProviderChoice[];
    readonly providerId: string | null;
    /** Why nothing can be requested (no provider, nothing in the box); null when something can. */
    readonly blocked: string | null;
    /** What the flow is doing, or its answer / question. */
    readonly message: string | null;
    readonly plan: WireGeometryPlan | null;
}

interface BoxRequestState {
    byBox: Readonly<Record<string, BoxRequestEntry>>;
    /** The provider last asked, offered first next time. */
    lastProvider: string | null;
    patch: (boxId: string, patch: Partial<BoxRequestEntry>) => void;
    clear: (boxId: string) => void;
}

export const useBoxRequestStore = create<BoxRequestState>()((set, get) => ({
    byBox: {},
    lastProvider: null,
    patch: (boxId, patch) => {
        const prev = get().byBox[boxId] ?? EMPTY_ENTRY;
        set({byBox: {...get().byBox, [boxId]: {...prev, ...patch}}});
    },
    clear: (boxId) => {
        const {[boxId]: _gone, ...rest} = get().byBox;
        set({byBox: rest});
    },
}));

const EMPTY_ENTRY: BoxRequestEntry = {
    phase: "loading",
    mode: "partial",
    choices: [],
    providerId: null,
    blocked: null,
    message: null,
    plan: null,
};

const BUSY: readonly BoxRequestPhase[] = ["planning", "requesting"];

export function isBoxRequestBusy(entry: BoxRequestEntry | undefined): boolean {
    return !!entry && BUSY.includes(entry.phase);
}

// --- the live flow ---------------------------------------------------------------------------------
// Heavy modules (three.js scene, the asset browser, the plugin listing) are imported on first use,
// so a test of this store, or a page that never opens the menu, does not load them.

async function scopeNow(): Promise<string> {
    const {scopeUrlPart, useScopeStore} = await import("@/state/scopeStore");
    return scopeUrlPart(useScopeStore.getState().current);
}

/** The box's members in `mode`, and how many draw ranges they came from. */
async function captureBox(boxId: string, mode: BoxSelectMode) {
    const {boxRanges} = await import("@/utils/bboxSelection/applyBoxSelection");
    const {captureRanges} = await import("@/utils/groups/groupScene");
    const batch = boxRanges(boxId, mode);
    if (batch.length === 0) {
        return {elements: 0, members: [] as GroupMember[], reason: "Nothing drawn is in the box."};
    }
    const captured = captureRanges(batch);
    return {elements: batch.length, members: captured.members, reason: captured.reason};
}

/** Open (or refresh) the box's request: which providers can be asked for what it holds in `mode`.
 *  A request in flight is left alone. */
export async function prepareBoxRequest(boxId: string, mode: BoxSelectMode): Promise<void> {
    const store = useBoxRequestStore.getState();
    if (isBoxRequestBusy(store.byBox[boxId])) return;
    store.patch(boxId, {phase: "loading", mode, message: null, plan: null, blocked: null});
    try {
        const [{members, reason}, scope] = await Promise.all([captureBox(boxId, mode), scopeNow()]);
        if (members.length === 0) {
            store.patch(boxId, {phase: "ready", choices: [], providerId: null, blocked: reason ?? "Nothing in the box."});
            return;
        }
        const [{pluginsApi}, {clashCheckApi}, {assetProviderCollections}, {useMeStore}, {providerName}] = await Promise.all([
            import("@/services/api/plugins"),
            import("@/services/api/clashCheck"),
            import("@/services/assetScopeCollections"),
            import("@/state/meStore"),
            import("@/state/providerNamesStore"),
        ]);
        const [plugins, readable] = await Promise.all([
            pluginsApi.listBackendPlugins().catch(() => ({plugins: [] as unknown[]})),
            clashCheckApi.listGeometryProviders(scope as ScopeUrl).catch(() => null),
        ]);
        const choices = boxRequestChoices({
            members,
            declared: assetProviderCollections(plugins.plugins ?? []),
            readable,
            isAdmin: useMeStore.getState().isAdmin,
            providerName,
            preferred: useBoxRequestStore.getState().lastProvider,
        });
        const current = useBoxRequestStore.getState().byBox[boxId];
        if (current && current.mode !== mode) return; // the mode changed while this was read
        const keep = choices.find((c) => c.providerId === current?.providerId);
        store.patch(boxId, {
            phase: "ready",
            choices,
            providerId: (keep ?? choices[0])?.providerId ?? null,
            blocked: choices.length ? null : noChoiceReason(members, readable),
        });
    } catch (e) {
        store.patch(boxId, {phase: "error", message: e instanceof Error ? e.message : String(e)});
    }
}

/** Ask the chosen provider which of the box's members it has no geometry for; the answer is the
 *  question to confirm (or "nothing to request"). */
export async function planBoxRequest(boxId: string, label: string): Promise<void> {
    const store = useBoxRequestStore.getState();
    const entry = store.byBox[boxId];
    if (!entry || isBoxRequestBusy(entry) || !entry.providerId) return;
    const choice = entry.choices.find((c) => c.providerId === entry.providerId);
    if (!choice) return;
    store.patch(boxId, {phase: "planning", message: `asking ${choice.label} what it has`, plan: null});
    try {
        const [{elements, members, reason}, scope] = await Promise.all([captureBox(boxId, entry.mode), scopeNow()]);
        if (members.length === 0) {
            store.patch(boxId, {phase: "ready", blocked: reason ?? "Nothing in the box.", message: null});
            return;
        }
        const {clashCheckApi} = await import("@/services/api/clashCheck");
        const plan = await clashCheckApi.geometryPlan(scope as ScopeUrl, {
            target: boxPlanTarget(label, members),
            geometryProvider: choice.providerId,
        });
        const summary = summariseBoxPlan(plan);
        useBoxRequestStore.setState({lastProvider: choice.providerId});
        store.patch(boxId, {
            phase: summary.missing ? "confirm" : "done",
            plan,
            message: boxRequestSentence(summary, choice.label, elements),
        });
    } catch (e) {
        store.patch(boxId, {phase: "error", message: e instanceof Error ? e.message : String(e)});
    }
}

/** Request exactly the planned members the provider lacks, then publish what it stages -- the clash
 *  check's and the selection export's request (`requestUnmatchedMembers`). */
export async function confirmBoxRequest(boxId: string): Promise<void> {
    const store = useBoxRequestStore.getState();
    const entry = store.byBox[boxId];
    if (!entry || entry.phase !== "confirm" || !entry.plan) return;
    const choice = entry.choices.find((c) => c.providerId === entry.providerId);
    if (!choice) return;
    const stage = (s: string | null) => {
        if (s) useBoxRequestStore.getState().patch(boxId, {message: s});
    };
    store.patch(boxId, {phase: "requesting", message: `requesting from ${choice.label}`});
    try {
        const scope = await scopeNow();
        const [{geometryRequestDeps}, {requestUnmatchedMembers}] = await Promise.all([
            import("@/state/clashGeometryDeps"),
            import("@/utils/groups/groupRequest"),
        ]);
        const deps = await geometryRequestDeps(scope, stage);
        const {notes, outcome} = await requestUnmatchedMembers(deps, scope, choice.providerId, choice.req, entry.plan.unmatched, stage);
        const failed = !outcome || outcome.failed.length > 0;
        const tail = outcome && outcome.published > 0 ? " Load the new geometry from the Sources tab." : "";
        store.patch(boxId, {
            phase: failed && !(outcome && outcome.published + outcome.unchanged > 0) ? "error" : "done",
            message: (notes.join(" ") || "Nothing was requested.") + tail,
        });
    } catch (e) {
        store.patch(boxId, {phase: "error", message: e instanceof Error ? e.message : String(e)});
    }
}
