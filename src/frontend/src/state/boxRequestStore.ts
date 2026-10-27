import {create} from "zustand";

import type {WireGeometryPlan} from "@/services/api/clashCheck";
import type {ScopeUrl} from "@/services/api/client";
import type {BoxSelectMode} from "@/utils/bboxSelection/boxMath";
import {
    boundsMembers,
    boundsMode,
    boxPlanTarget,
    boxRequestChoices,
    boxRequestSentence,
    defaultBoxProvider,
    noChoiceReason,
    pickBoundsSets,
    presentMembers,
    sceneBoxToSource,
    summariseBoxPlan,
    type BoxCandidates,
    type RequestProviderChoice,
} from "@/utils/bboxSelection/boxRequest";
import type {GroupMember} from "@/utils/groups/savedGroups";

// "Request from source" on a selection box (see `utils/bboxSelection/boxRequest.ts`): one request
// per box at a time, kept here so it outlives the box's menu -- the Scene panel's box list shows
// where it has got to. The provider jobs themselves are in the global job toast.

export type BoxRequestPhase = "loading" | "ready" | "planning" | "confirm" | "requesting" | "placing" | "done" | "error";

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
    /** Where the box's members come from: the published bounds, or (without them) what is drawn. */
    readonly candidates: BoxCandidates | null;
    /** The plan's members the provider already has that are not in the scene: what "Load" puts
     *  there. */
    readonly loadable: readonly GroupMember[];
}

interface BoxRequestState {
    byBox: Readonly<Record<string, BoxRequestEntry>>;
    /** The provider the user last picked in a box's "From" list: the default next time, while it
     *  still qualifies. */
    lastProvider: string | null;
    patch: (boxId: string, patch: Partial<BoxRequestEntry>) => void;
    /** An explicit pick in the "From" list. */
    chooseProvider: (boxId: string, providerId: string) => void;
    clear: (boxId: string) => void;
}

export const useBoxRequestStore = create<BoxRequestState>()((set, get) => ({
    byBox: {},
    lastProvider: null,
    patch: (boxId, patch) => {
        const prev = get().byBox[boxId] ?? EMPTY_ENTRY;
        set({byBox: {...get().byBox, [boxId]: {...prev, ...patch}}});
    },
    chooseProvider: (boxId, providerId) => {
        set({lastProvider: providerId});
        get().patch(boxId, {providerId, phase: "ready", plan: null, message: null});
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
    candidates: null,
    loadable: [],
};

const BUSY: readonly BoxRequestPhase[] = ["planning", "requesting", "placing"];

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

/** The box's DRAWN members in `mode`, and how many draw ranges they came from. */
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

/** The published models in the scene: provider, collection, and where the loader put each one's
 *  model frame (the world position of its source-space root -- the scene's recentring offset). */
async function loadedModels(): Promise<{provider: string; collection: string; offset: [number, number, number] | null}[]> {
    const [{getSingletonViewerStores}, {loadedSourceGroups}, {sourceSpaceRoot}, THREE] = await Promise.all([
        import("@/state/AdaViewerContext"),
        import("@/state/modelState"),
        import("@/utils/scene/clashJointMarkers"),
        import("three"),
    ]);
    // The scene's one recentring offset, for a model whose group is not (or no longer) registered.
    const fallback = getSingletonViewerStores().useModelState.getState().translation;
    const base: [number, number, number] | null = fallback ? [fallback.x, fallback.y, fallback.z] : null;
    return getSingletonViewerStores().useAssetBrowserStore.getState().loaded.map((a) => {
        const group = loadedSourceGroups.get(a.sourceName);
        let offset = base;
        if (group) {
            const root = sourceSpaceRoot(group);
            root.updateWorldMatrix(true, false);
            const p = root.getWorldPosition(new THREE.Vector3());
            offset = [p.x, p.y, p.z];
        }
        return {provider: a.ref.provider, collection: a.ref.collection.toLowerCase(), offset};
    });
}

/** What the box holds in `mode`: every published node in it, from the providers' node bounds,
 *  loaded or not -- or, where no provider published bounds for the scene's collections, what is
 *  drawn in it (`BoxCandidates` says which, and why). */
async function findCandidates(
    boxId: string,
    mode: BoxSelectMode,
): Promise<{members: GroupMember[]; candidates: BoxCandidates; reason: string | null; rankFirst: string[]}> {
    const [drawn, {useBBoxSelectionStore}] = await Promise.all([captureBox(boxId, mode), import("@/state/bboxSelectionStore")]);
    const box = useBBoxSelectionStore.getState().boxes.find((b) => b.id === boxId);
    const models = await loadedModels().catch(() => []);
    // The scene's own providers, most models first: the default source to ask (the members of a
    // bounds answer are the BOUNDS provider's, which need not be the one the scene was loaded from).
    const counts = new Map<string, number>();
    for (const m of models) counts.set(m.provider, (counts.get(m.provider) ?? 0) + 1);
    const ranked = [...counts.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(([p]) => p);
    const collections = [
        ...new Set([
            ...models.map((m) => m.collection),
            ...drawn.members.flatMap((m) => (m.target.kind === "node" ? [m.target.collection.toLowerCase()] : [])),
        ]),
    ];
    let why: string | null = null;
    if (box && collections.length) {
        try {
            const [{nodeBoundsApi}, scope] = await Promise.all([import("@/services/api/nodeBounds"), scopeNow()]);
            const own = models.find((m) => m.offset !== null);
            const res = await nodeBoundsApi.search(scope as ScopeUrl, {
                box: sceneBoxToSource({min: box.min, max: box.max}, own?.offset ?? null),
                mode: boundsMode(mode),
                collections,
            });
            if (res.sets.length) {
                const picked = pickBoundsSets(res.sets, ranked);
                const members = boundsMembers(res.hits, picked);
                const providers = [...new Set([...picked.values()].map((s) => s.provider))];
                return {
                    members,
                    candidates: {kind: "bounds", providers, truncated: res.truncated},
                    reason: members.length ? null : res.truncated ?? "Nothing published is in the box.",
                    rankFirst: ranked,
                };
            }
            why = `no source published node bounds for ${collections.join(", ")}`;
        } catch (e) {
            why = `the published bounds could not be read (${e instanceof Error ? e.message : String(e)})`;
        }
    } else if (!collections.length) {
        why = "nothing published is in the scene";
    }
    return {
        members: drawn.members,
        candidates: {kind: "drawn", elements: drawn.elements, why},
        reason: drawn.reason,
        rankFirst: [],
    };
}

/** Open (or refresh) the box's request: which providers can be asked for what it holds in `mode`.
 *  A request in flight is left alone. */
export async function prepareBoxRequest(boxId: string, mode: BoxSelectMode): Promise<void> {
    const store = useBoxRequestStore.getState();
    if (isBoxRequestBusy(store.byBox[boxId])) return;
    store.patch(boxId, {phase: "loading", mode, message: null, plan: null, blocked: null, loadable: []});
    try {
        const {members, candidates, reason, rankFirst} = await findCandidates(boxId, mode);
        if (members.length === 0) {
            store.patch(boxId, {phase: "ready", choices: [], providerId: null, candidates, blocked: reason ?? "Nothing in the box."});
            return;
        }
        const [{pluginsApi}, {assetProviderCollections}, {useMeStore}, {providerName}] = await Promise.all([
            import("@/services/api/plugins"),
            import("@/services/assetScopeCollections"),
            import("@/state/meStore"),
            import("@/state/providerNamesStore"),
        ]);
        const plugins = await pluginsApi.listBackendPlugins().catch(() => ({plugins: [] as unknown[]}));
        const choices = boxRequestChoices({
            members,
            declared: assetProviderCollections(plugins.plugins ?? []),
            isAdmin: useMeStore.getState().isAdmin,
            providerName,
            rankFirst,
        });
        const current = useBoxRequestStore.getState().byBox[boxId];
        if (current && current.mode !== mode) return; // the mode changed while this was read
        store.patch(boxId, {
            phase: "ready",
            choices,
            candidates,
            providerId: defaultBoxProvider(choices, useBoxRequestStore.getState().lastProvider),
            blocked: choices.length ? null : noChoiceReason(members),
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
    store.patch(boxId, {phase: "planning", message: `finding what is in the box`, plan: null, loadable: []});
    try {
        const [{members, candidates, reason}, scope] = await Promise.all([findCandidates(boxId, entry.mode), scopeNow()]);
        if (members.length === 0) {
            store.patch(boxId, {phase: "ready", candidates, blocked: reason ?? "Nothing in the box.", message: null});
            return;
        }
        store.patch(boxId, {candidates, message: `asking ${choice.label} what it has of ${members.length} member(s)`});
        const {clashCheckApi} = await import("@/services/api/clashCheck");
        const plan = await clashCheckApi.geometryPlan(scope as ScopeUrl, {
            target: boxPlanTarget(label, members),
            geometryProvider: choice.providerId,
        });
        const summary = summariseBoxPlan(plan);
        const loadable = await notInScene(presentMembers(plan));
        const others = entry.choices.length > 1 && !summary.missing ? " Pick another source under From to ask it instead." : "";
        const load = loadable.length ? ` ${loadable.length} of what it has ${loadable.length === 1 ? "is" : "are"} not in the scene.` : "";
        store.patch(boxId, {
            phase: summary.missing ? "confirm" : "done",
            plan,
            loadable,
            message: boxRequestSentence(summary, choice.label, candidates) + load + others,
        });
    } catch (e) {
        store.patch(boxId, {phase: "error", message: e instanceof Error ? e.message : String(e)});
    }
}

/** A one-off group of `members` -- the shape a saved group's load and selection read. */
function boxGroup(label: string, members: readonly GroupMember[]) {
    return {id: `box:${label}`, name: label, created_at: new Date().toISOString(), members};
}

/** Those of `members` whose model is not in the scene, by the same matching a saved group's
 *  "Select" and "Load into scene" use (`groupSceneParts`, read only). */
async function notInScene(members: readonly GroupMember[]): Promise<GroupMember[]> {
    if (members.length === 0) return [];
    try {
        const {groupSceneParts} = await import("@/utils/groups/groupScene");
        return [...groupSceneParts(members).notLoaded];
    } catch {
        return [];
    }
}

/** Put the box's members the chosen provider already has, and that are not in the scene, in the
 *  scene -- a saved group's "Load into scene" (`loadSavedGroup`), which loads each source once and
 *  then selects them. */
export async function loadBoxMembers(boxId: string, label: string): Promise<void> {
    const store = useBoxRequestStore.getState();
    const entry = store.byBox[boxId];
    if (!entry || isBoxRequestBusy(entry) || entry.loadable.length === 0) return;
    const back = entry.phase;
    const stage = (s: string) => useBoxRequestStore.getState().patch(boxId, {message: s});
    store.patch(boxId, {phase: "placing", message: `loading ${entry.loadable.length} into the scene`});
    try {
        const [scope, {loadSavedGroup}, {realDeliveryDeps}, {getSingletonViewerStores}] = await Promise.all([
            scopeNow(),
            import("@/utils/groups/groupLoad"),
            import("@/components/asset_browser/AssetsTab"),
            import("@/state/AdaViewerContext"),
        ]);
        const deps = realDeliveryDeps((name) => getSingletonViewerStores().useModelState.getState().loadedSourceNames.has(name));
        const out = await loadSavedGroup(boxGroup(label, entry.loadable), scope, deps, stage);
        const failed = out.failed.length
            ? ` ${out.failed.length} could not be loaded: ${out.failed[0].reason}${out.failed.length > 1 ? " (and others)" : ""}.`
            : "";
        store.patch(boxId, {
            phase: back === "confirm" ? "confirm" : out.loaded || !out.failed.length ? "done" : "error",
            loadable: out.failed.flatMap((f) => f.members),
            message: `Loaded ${out.loaded} model${out.loaded === 1 ? "" : "s"} into the scene` +
                (out.already ? `, ${out.already} already there` : "") + "." + failed,
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
