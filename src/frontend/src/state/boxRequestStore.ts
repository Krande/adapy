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
import type {Operation} from "@/services/operationTracking";
import type {BoxSiteLoadOutcome} from "@/utils/bboxSelection/boxSiteLoad";

// "Request from source" on a selection box (see `utils/bboxSelection/boxRequest.ts`): one request
// per box at a time, kept here so it outlives the box's menu -- the Scene panel's box list shows
// where it has got to. The provider jobs themselves are in the global job toast.
//
// INTO THE SCENE AS WHOLE SITES. What the box holds -- loaded from the provider ("Load"), or once a
// request has published it -- goes into the scene as the sites it belongs to, one model each with
// its whole tree, everything outside the box hidden (`utils/bboxSelection/boxSiteLoad`): what the
// box missed is shown from the scene tree.
//
// NO BOUNDS YET. Without published node bounds for the scene's collections the box only sees what
// is drawn. When a scene provider can measure them (its sweep offers `sweep_target: bounds`), the
// entry carries that as `boundsOffer`, and `requestBoxBounds` runs it for those collections; when
// the run ends, every box waiting on it reads its choices again.

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
    /** Node bounds a scene provider could measure for collections that have none; null when there is
     *  nothing to offer. */
    readonly boundsOffer: BoundsOffer | null;
}

export interface BoundsOffer {
    readonly provider: string;
    readonly collections: readonly string[];
    /** Whether this user may ask; `reason` says why not. */
    readonly allowed: boolean;
    readonly reason: string | null;
    /** The run measuring them, once asked. */
    readonly runId: string | null;
    readonly error: string | null;
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
    boundsOffer: null,
};

/** `collection \0 node id` -> the node's full name, from every bounds answer read: how a box member
 *  is found in its site's scene tree (`boxSiteLoad`), which names rows, not node ids. */
const boundsNames = new Map<string, string>();

/** Bounds runs in flight, by `boundsKey`: one per (provider, collections), whichever box asked. */
const boundsRuns = new Map<string, string>();

function boundsKey(provider: string, collections: readonly string[]): string {
    return `${provider}|${[...collections].sort().join(",")}`;
}

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
): Promise<{
    members: GroupMember[];
    candidates: BoxCandidates;
    reason: string | null;
    rankFirst: string[];
    boundsOffer: BoundsOffer | null;
}> {
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
    let boundsOffer: BoundsOffer | null = null;
    if (box && collections.length) {
        try {
            const [{nodeBoundsApi}, scope] = await Promise.all([import("@/services/api/nodeBounds"), scopeNow()]);
            const own = models.find((m) => m.offset !== null);
            const res = await nodeBoundsApi.search(scope as ScopeUrl, {
                box: sceneBoxToSource({min: box.min, max: box.max}, own?.offset ?? null),
                mode: boundsMode(mode),
                collections,
            });
            for (const h of res.hits) {
                const name = h.full_name ?? h.label;
                if (name) boundsNames.set(`${h.collection.toLowerCase()}\u0000${h.node_id}`, name);
            }
            if (res.sets.length) {
                const picked = pickBoundsSets(res.sets, ranked);
                const members = boundsMembers(res.hits, picked);
                const providers = [...new Set([...picked.values()].map((s) => s.provider))];
                // Bounds that hold nothing where something IS drawn miss part of the project (a
                // site the extraction could not read): what is drawn, and the offer to extract again.
                if (members.length || res.truncated || !drawn.members.length) {
                    return {
                        members,
                        candidates: {kind: "bounds", providers, truncated: res.truncated},
                        reason: members.length ? null : res.truncated ?? "Nothing published is in the box.",
                        rankFirst: ranked,
                        boundsOffer: null,
                    };
                }
                why = `${providers.join(", ")}'s published bounds hold nothing here, though something is drawn`;
            } else {
                why = `no source published node bounds for ${collections.join(", ")}`;
            }
            boundsOffer = await offerBounds(scope as ScopeUrl, ranked, models);
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
        boundsOffer,
    };
}

/** The first scene provider, most models first, that can measure node bounds for its collections in
 *  the scene -- one the user may ask before one they may not. Null when none can. */
async function offerBounds(
    scope: ScopeUrl,
    ranked: readonly string[],
    models: readonly {provider: string; collection: string}[],
): Promise<BoundsOffer | null> {
    const {nodeBoundsApi} = await import("@/services/api/nodeBounds");
    let refused: BoundsOffer | null = null;
    for (const provider of ranked) {
        const permission = await nodeBoundsApi.requestPermission(scope, provider).catch(() => null);
        if (!permission?.available) continue;
        const collections = [...new Set(models.filter((m) => m.provider === provider).map((m) => m.collection))];
        const offer: BoundsOffer = {
            provider,
            collections,
            allowed: permission.allowed,
            reason: permission.reason,
            runId: boundsRuns.get(boundsKey(provider, collections)) ?? null,
            error: null,
        };
        if (offer.allowed) return offer;
        refused ??= offer;
    }
    return refused;
}

/** Ask the box's offered provider to measure and publish node bounds for the offered collections.
 *  The run is in the global toast; when it ends, every box offered the same run reads its choices
 *  again -- now from the bounds, if the run published them. */
export async function requestBoxBounds(boxId: string): Promise<void> {
    const store = useBoxRequestStore.getState();
    const offer = store.byBox[boxId]?.boundsOffer;
    if (!offer || !offer.allowed || offer.runId) return;
    const key = boundsKey(offer.provider, offer.collections);
    const setOffer = (id: string, patch: Partial<BoundsOffer>) => {
        const entry = useBoxRequestStore.getState().byBox[id];
        if (entry?.boundsOffer && boundsKey(entry.boundsOffer.provider, entry.boundsOffer.collections) === key) {
            useBoxRequestStore.getState().patch(id, {boundsOffer: {...entry.boundsOffer, ...patch}});
        }
    };
    try {
        const [{nodeBoundsApi}, {trackBoundsRequest}, scope] = await Promise.all([
            import("@/services/api/nodeBounds"),
            import("@/services/sweepTracking"),
            scopeNow(),
        ]);
        const run = await nodeBoundsApi.request(scope as ScopeUrl, offer.provider, offer.collections);
        boundsRuns.set(key, run.id);
        for (const id of Object.keys(useBoxRequestStore.getState().byBox)) setOffer(id, {runId: run.id, error: null});
        trackBoundsRequest(scope, run.id, `Node bounds · ${offer.provider} · ${offer.collections.join(", ")}`, () => {
            boundsRuns.delete(key);
            for (const [id, entry] of Object.entries(useBoxRequestStore.getState().byBox)) {
                if (entry.boundsOffer?.runId === run.id) {
                    setOffer(id, {runId: null});
                    void prepareBoxRequest(id, entry.mode);
                }
            }
        });
    } catch (e) {
        setOffer(boxId, {error: e instanceof Error ? e.message : String(e)});
    }
}

/** Open (or refresh) the box's request: which providers can be asked for what it holds in `mode`.
 *  A request in flight is left alone. */
export async function prepareBoxRequest(boxId: string, mode: BoxSelectMode): Promise<void> {
    const store = useBoxRequestStore.getState();
    if (isBoxRequestBusy(store.byBox[boxId])) return;
    store.patch(boxId, {phase: "loading", mode, message: null, plan: null, blocked: null, loadable: []});
    try {
        const {members, candidates, reason, rankFirst, boundsOffer} = await findCandidates(boxId, mode);
        if (members.length === 0) {
            store.patch(boxId, {
                phase: "ready",
                choices: [],
                providerId: null,
                candidates,
                boundsOffer,
                blocked: reason ?? "Nothing in the box.",
            });
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
            boundsOffer,
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
        const [{members, candidates, reason, boundsOffer}, scope] = await Promise.all([
            findCandidates(boxId, entry.mode),
            scopeNow(),
        ]);
        if (members.length === 0) {
            store.patch(boxId, {phase: "ready", candidates, boundsOffer, blocked: reason ?? "Nothing in the box.", message: null});
            return;
        }
        store.patch(boxId, {candidates, boundsOffer, message: `asking ${choice.label} what it has of ${members.length} member(s)`});
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
/** `members` into the scene as their whole sites, everything outside the box hidden
 *  (`utils/bboxSelection/boxSiteLoad`), as steps of `op`. */
async function placeBoxSites(
    members: readonly GroupMember[],
    op: Operation,
    stage: (s: string) => void,
): Promise<BoxSiteLoadOutcome> {
    const [scope, {loadBoxSites}, {realDeliveryDeps}, {getSingletonViewerStores}] = await Promise.all([
        scopeNow(),
        import("@/utils/bboxSelection/boxSiteLoad"),
        import("@/components/asset_browser/AssetsTab"),
        import("@/state/AdaViewerContext"),
    ]);
    const deps = realDeliveryDeps(
        (name) => getSingletonViewerStores().useModelState.getState().loadedSourceNames.has(name),
        op.trackJob,
    );
    const out = await loadBoxSites(members, scope, deps, boundsNames, stage);
    for (const f of out.failed) op.fail(`${f.site}: ${f.reason}`);
    return out;
}

function boxSitesSentence(out: BoxSiteLoadOutcome): string {
    const sites = (n: number) => `${n} site${n === 1 ? "" : "s"}`;
    const parts = [
        out.loaded ? `Loaded ${sites(out.loaded)} into the scene, everything outside the box hidden` : "",
        out.already ? `${sites(out.already)} already there, the box's part shown` : "",
    ].filter(Boolean);
    const failed = out.failed.length
        ? ` ${out.failed.length} could not be loaded: ${out.failed[0].site}: ${out.failed[0].reason}${out.failed.length > 1 ? " (and others)" : ""}.`
        : "";
    return (parts.length ? `${parts.join("; ")}. Show the rest from the scene tree.` : "Nothing was loaded.") + failed;
}

export async function loadBoxMembers(boxId: string, label: string): Promise<void> {
    const store = useBoxRequestStore.getState();
    const entry = store.byBox[boxId];
    if (!entry || isBoxRequestBusy(entry) || entry.loadable.length === 0) return;
    const back = entry.phase;
    store.patch(boxId, {phase: "placing", message: `loading ${entry.loadable.length} into the scene`});
    // One operation in the toast for every model loaded: their builds and downloads rows under it.
    const {beginOperation} = await import("@/services/operationTracking");
    const op = beginOperation(`Loading ${entry.loadable.length} from ${label}`);
    const release = op.hold();
    const stage = (s: string) => {
        useBoxRequestStore.getState().patch(boxId, {message: s});
        op.stage(s);
    };
    try {
        const out = await placeBoxSites(entry.loadable, op, stage);
        store.patch(boxId, {
            phase: back === "confirm" ? "confirm" : out.loaded + out.already || !out.failed.length ? "done" : "error",
            loadable: [],
            message: boxSitesSentence(out),
        });
    } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        op.fail(message);
        store.patch(boxId, {phase: "error", message});
    } finally {
        release();
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
    store.patch(boxId, {phase: "requesting", message: `requesting from ${choice.label}`});
    // One operation in the toast for the whole request: every provider job and publish a row under it.
    const {beginOperation} = await import("@/services/operationTracking");
    const op = beginOperation(`Request from ${choice.label} · ${entry.plan.unmatched.length} member(s)`);
    const release = op.hold();
    const stage = (s: string | null) => {
        if (s) useBoxRequestStore.getState().patch(boxId, {message: s});
    };
    try {
        const scope = await scopeNow();
        const [{geometryRequestDeps}, {requestUnmatchedMembers}] = await Promise.all([
            import("@/state/clashGeometryDeps"),
            import("@/utils/groups/groupRequest"),
        ]);
        const deps = await geometryRequestDeps(scope, stage, op.trackJob);
        const {notes, outcome} = await requestUnmatchedMembers(deps, scope, choice.providerId, choice.req, entry.plan.unmatched, stage);
        for (const f of outcome?.failed ?? []) op.fail(f);
        const failed = !outcome || outcome.failed.length > 0;
        const landed = !!outcome && outcome.published + outcome.unchanged > 0;
        let tail = "";
        if (landed) {
            // What the box holds is now at the provider: into the scene as whole sites, the rest of
            // each hidden -- asked again, so the members are the provider's own, as published.
            const placing = (s: string) => {
                stage(s);
                op.stage(s);
            };
            placing("placing what was requested in the scene");
            const {members} = await findCandidates(boxId, entry.mode);
            const {clashCheckApi} = await import("@/services/api/clashCheck");
            const plan = await clashCheckApi.geometryPlan(scope as ScopeUrl, {
                target: boxPlanTarget("Box", members),
                geometryProvider: choice.providerId,
            });
            const placed = await placeBoxSites(presentMembers(plan), op, placing);
            tail = ` ${boxSitesSentence(placed)}`;
        }
        store.patch(boxId, {
            phase: failed && !landed ? "error" : "done",
            loadable: [],
            message: (notes.join(" ") || "Nothing was requested.") + tail,
        });
    } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        op.fail(message);
        store.patch(boxId, {phase: "error", message});
    } finally {
        release();
    }
}
