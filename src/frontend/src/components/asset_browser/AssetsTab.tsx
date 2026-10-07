// The Assets tab: published collections in this scope, as one lazily-assembled
// forest read through ONE derived `AssetView`.
//
// Data flow, one direction only:
//
//   routes --(loader)--> store: index, forest, mode, expansion   (inputs)
//   store --(buildAssetView, memoised)--> view                    (derived)
//   view + id --(rowFacts)--> every badge a row shows             (rendered)
//
// Nothing below the view computes a revision for itself. The hierarchy half of
// the view is memoised on the forest alone, so a mode switch re-derives the
// resolution passes without re-indexing a 41k-row spine.
//
// No delivery yet (Phase 3): a row says what is published and where, and a
// selected row's detail says what claim it carries; loading into the scene
// comes with the delivery kinds.

import React, { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { create } from "zustand";

import { revisionsOf } from "@/assets/assetIndex";
import { buildAssetHierarchy, buildAssetView, type AssetView } from "@/assets/assetView";
import {
    VIEW_DOC_SCHEMA,
    displayHierarchy,
    isOutOfScope,
    normKind,
    resolveTreeView,
    viewDocFor,
    type TreeViewDoc,
} from "@/assets/treeView";
import type { ChangeState } from "@/assets/changes";
import { nodeBatches, requestNodes, type NodeTarget } from "@/assets/collectionRequest";
import {
    assetSourceName,
    loadPrepared,
    parseDeliveryClaim,
    prepareNode,
    type LoadNodeDeps,
    type NodeRef,
    type PreparedNode,
} from "@/assets/delivery";
import { geometryIndex, rowHasGeometry, rowLoadable } from "@/assets/geometryMarks";
import { orphanHeading, orphanSentence, type OrphanEntry } from "@/assets/orphans";
import { MIN_SEARCH_CHARS, changeOwners, isSearchTerm, rowFacts, subjectsByOwner, type RowBadge } from "@/assets/rowFacts";
import { levelKey, levelWanted } from "@/assets/spines";
import { actionTargets } from "@/assets/treeKeys";
import { bothKeep, loadsProvider, membersForIds, treeSetFilter, type TreeSet } from "@/assets/treeSets";
import { beginBulkLoad, endBulkLoad } from "@/utils/scene/loadingView";
import type { ResolutionMode, WireNodeAttributes } from "@/assets/types";
import PositionedMenu, { type KebabMenuItem } from "@/components/common/PositionedMenu";
import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { makePluginContextStandalone } from "@/plugins";
import { assetsApi } from "@/services/api/assets";
import { authHeader, type ScopeUrl } from "@/services/api/client";
import { fetchAssetAttributes } from "@/services/assets";
import { conversionApi } from "@/services/api/conversion";
import { filesApi } from "@/services/api/files";
import { sourceNodesApi } from "@/services/api/sourceNodes";
import { readViewDoc, writeViewDoc } from "@/services/assetView";
import { assetProviderCollections, type AssetNodeRequest, type AssetRequestOptions } from "@/services/assetScopeCollections";
import { viewerApi } from "@/services/viewerApi";
import { useClashCheckStore } from "@/state/clashCheckStore";
import { useMeStore } from "@/state/meStore";
import { useSceneInfoStore } from "@/state/sceneInfoStore";
import { useTreeSetsStore } from "@/state/treeSetsStore";
import { useViewerStores } from "@/state/AdaViewerContext";
import { loaderFor } from "@/state/assetBrowserLoader";
import { useModelSessionStore } from "@/state/modelSession";
import { requestRender } from "@/state/perfStore";
import { scopeUrlPart } from "@/state/scopeStore";
import { getViewerRuntime } from "@/state/viewerRuntime";
import { selectTreeNode } from "@/utils/tree_view/treeNavigation";

import AssetTree from "./AssetTree";
import { formatRevision } from "./format";
import RequestCollection, { requestDeps } from "./RequestCollection";
import TreeLegend from "./TreeLegend";
import ProviderOptionsPanel from "./ProviderOptionsPanel";
import TreeOptionsPanel from "./TreeOptionsPanel";
import TreeSetsPanel from "./TreeSetsPanel";
import TreeViewPanel, { type TreeViewChange } from "./TreeViewPanel";

// Owner tag for every scene object this tab adds -- the same role `OWNER` in
// `ExternalModelsPanel.tsx` plays for the External Models panel: a standalone
// plugin context is the documented way for CORE UI (not just a plugin) to
// reach `SceneHandle.loadModelFromUrl`/`unloadModel`, so loading and unloading
// go through the exact path a plugin would use rather than a second one.
const OWNER = "assets";

/** `state/model_worker/cacheModelUtils.ts`'s synthetic container id -- every
 *  loaded model's tree root is one of its children (or the tree root itself,
 *  when only one model is loaded). Mirrored here rather than imported because
 *  that module exports no constant, only the behaviour. */
const ROOTS_CONTAINER_ID = "__roots__";

/** Real `LoadNodeDeps` for `loadNode`: REST calls through `assetsApi` /
 *  `conversionApi` (the same job-status route a plugin job polls), the scene
 *  through a standalone plugin context, and `useModelState.loadedSourceNames`
 *  for "is this already loaded". Built fresh per call rather than memoised --
 *  every field it closes over is either a stable module export or a snapshot
 *  read at call time, so there is nothing to keep in sync. */
export function realDeliveryDeps(isLoaded: (sourceName: string) => boolean): LoadNodeDeps {
    return {
        api: {
            buildAssetNode: (scope, body) => assetsApi.buildAssetNode(scope, body),
            getBuildSummary: (scope, key) => assetsApi.getBuildSummary(scope, key),
            async jobStatus(jobId) {
                const status = await conversionApi.convertStatus(jobId);
                return { status: status.status, error: status.error };
            },
        },
        loadModelFromUrl: (owner, url, opts) => makePluginContextStandalone(OWNER).scene.loadModelFromUrl(owner, url, opts),
        isLoaded,
        blobUrl: (scope, key) => filesApi.blobUrl(scope, key),
        blobHeaders: () => authHeader(),
        trackJob: (opts) => {
            makePluginContextStandalone(OWNER).trackJob(opts);
        },
    };
}

/** The `NodeRef` a Load click on row `id` targets: `badge.at` (the manifest-
 *  owning subject -- the clicked row itself for a `solid` badge, the covering
 *  ancestor for `ghost`/`below`) carries the request; `id` rides along as
 *  `node` only when it differs, so two different covered rows stay separately
 *  revealable (`assetSourceName`) even though both load the same content. */
function refForBadge(view: AssetView, id: string, badge: RowBadge): NodeRef | null {
    const owner = view.hierarchy.byId.get(badge.at)?.data;
    if (!owner) return null;
    return {
        // The claim's provider -- its manifest's -- not the owner row's: the row names whichever
        // provider's spine merged last, and would send a load to the wrong publish.
        provider: badge.provider || owner.provider,
        collection: view.collection,
        subject: badge.at,
        revision: badge.revision,
        node: badge.at === id ? undefined : id,
    };
}

/** The Files-tab tree root for an already-loaded source, if the tree has been built at all.
 *
 * TWO NAMES, AND THEY ARE NOT THE SAME NAME. A load registers its group under the SOURCE NAME
 * (`registerLoadedSource`), while `cacheAndBuildTree` stamps the tree root's `model_key` with the
 * runtime MODEL KEY -- `<scene name>_<uuid>`, minted inside the loader. Matching the root by
 * source name therefore never matched anything, and the reveal action sat permanently disabled
 * beside a model that was plainly on screen.
 *
 * The bridge between the two is the group object itself, which is exactly how the unload path
 * resolves the same question (`unload_source_from_scene`): find the group registered under this
 * source name, then find the key it is stored under in the runtime's model-key map. */
function loadedTreeRoot(treeData: TreeNodeData | null, sourceName: string): TreeNodeData | null {
    if (!treeData) return null;
    const group = useModelSessionStore.getState().current()?.groups.get(sourceName);
    if (!group) return null;
    let modelKey: string | null = null;
    getViewerRuntime().modelKeyMap.current?.forEach((g, key) => {
        if (g === group) modelKey = key;
    });
    if (modelKey === null) return null;
    const candidates = treeData.id === ROOTS_CONTAINER_ID ? treeData.children : [treeData];
    return candidates.find((c) => c.model_key === modelKey) ?? null;
}

// Row-detail wording for the three non-`behind` states (`behind` gets its own
// sentence above, with the change itself). Kept as data so the four states'
// words are declared once rather than re-typed at each render.
const CHANGE_STATE_LABEL: Record<ChangeState, string> = {
    behind: "behind — see the line above",
    current: "up to date — the change feed covered this root and found nothing newer",
    "not-recorded": "not recorded — the change feed has never covered this root",
    "no-feed": "unknown — this deployment has no change-feed database",
};

const Banner: React.FC<{ tone: "info" | "warn" | "error"; children: React.ReactNode; title?: string }> = ({
    tone,
    children,
    title,
}) => (
    <div
        title={title}
        className={`mx-1 mt-1 rounded-sm px-2 py-1 text-xs ${
            tone === "error"
                ? "bg-red-900/60 text-red-100"
                : tone === "warn"
                  ? "bg-amber-900/50 text-amber-100"
                  : "bg-gray-700 text-gray-200"
        }`}
    >
        {children}
    </div>
);

/** The toolbar's one control surface: selects, the search field and icon buttons share it. */
const CONTROL = "h-7 rounded-md border border-gray-700 bg-gray-800 text-xs text-gray-100";
/** Buttons in the detail block: one primary action, the rest secondary or quiet. */
const BTN_PRIMARY =
    "h-7 px-3 rounded-md text-xs font-semibold bg-blue-400 text-gray-950 hover:bg-blue-300 disabled:opacity-50";
const BTN_SECONDARY =
    "h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50";
const BTN_QUIET ="h-7 px-1.5 rounded-md text-xs text-gray-400 hover:text-white disabled:opacity-50";

const IconButton: React.FC<{ label: string; pressed?: boolean; onClick: () => void; children: React.ReactNode }> = ({
    label,
    pressed,
    onClick,
    children,
}) => (
    <button
        type="button"
        aria-label={label}
        aria-pressed={pressed}
        title={label}
        onClick={onClick}
        className={`${CONTROL} w-7 shrink-0 grid place-items-center ${
            pressed ? "bg-gray-600 border-gray-500 text-white" : "text-gray-300 hover:text-white hover:bg-gray-700"
        }`}
    >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            {children}
        </svg>
    </button>
);

const ModePicker: React.FC<{ mode: ResolutionMode; revisions: readonly string[]; onChange: (m: ResolutionMode) => void }> = ({
    mode,
    revisions,
    onChange,
}) => {
    const newest = revisions[revisions.length - 1];
    return (
        <div className="flex items-center gap-1.5 min-w-0 shrink-0">
            <select
                aria-label="Resolution"
                className={`${CONTROL} px-2`}
                value={mode.kind}
                onChange={(e) => {
                    const kind = e.target.value as ResolutionMode["kind"];
                    if (kind === "latest") onChange({ kind });
                    else if (newest) onChange({ kind, revision: mode.kind === "latest" ? newest : mode.revision });
                }}
                title="latest: newest per subject (may mix publishes) · as of: newest at or before a revision · run: exactly one publish (coeval)"
            >
                <option value="latest">Latest</option>
                <option value="as-of" disabled={!newest}>As of</option>
                <option value="run" disabled={!newest}>Run</option>
            </select>
            {mode.kind !== "latest" && (
                <select
                    aria-label="Revision"
                    className={`${CONTROL} px-2 min-w-0 truncate`}
                    value={mode.revision}
                    onChange={(e) => onChange({ kind: mode.kind, revision: e.target.value })}
                >
                    {[...revisions].reverse().map((r) => (
                        <option key={r} value={r}>
                            {formatRevision(r)}
                        </option>
                    ))}
                </select>
            )}
        </div>
    );
};

/** Published subjects no loaded tree places, under the collection root.
 *  Collapsed to one line by default: the full sentence is on the entry's title
 *  and in the detail block when selected, so a long list cannot push the tree
 *  off a small screen. */
const Orphans: React.FC<{
    orphans: readonly OrphanEntry[];
    pending: readonly string[];
    unmergedSpines: number;
    loadingSpines: boolean;
    selected: string | null;
    onSelect: (id: string) => void;
    onPlace: () => void;
}> = ({ orphans, pending, unmergedSpines, loadingSpines, selected, onSelect, onPlace }) => {
    const [open, setOpen] = React.useState(false);
    if (pending.length) {
        // Not orphans yet: their branches are unopened. Neutral colour, and the
        // action that turns "not placed yet" into a real answer.
        return (
            <div className="border-t border-gray-700 text-xs shrink-0 flex items-center px-2 py-0.5 text-gray-300" data-testid="asset-pending">
                <span className="min-w-0 truncate" title={pending.join("\n")}>
                    {pending.length} published subject(s) not placed yet — their branches are unopened
                </span>
                {/* One level per unopened hierarchy, never a whole one. Once every
                    hierarchy is open, what is still unplaced sits under a branch
                    nobody has expanded, and expanding it is how it is placed. */}
                {unmergedSpines > 0 && (
                    <button
                        type="button"
                        className="ml-auto shrink-0 pl-2 text-blue-300 hover:text-white disabled:text-gray-500"
                        disabled={loadingSpines}
                        onClick={onPlace}
                        title={`Open the first level of the ${unmergedSpines} unopened published hierarch${unmergedSpines === 1 ? "y" : "ies"} to place them`}
                    >
                        {loadingSpines ? "placing…" : "place"}
                    </button>
                )}
            </div>
        );
    }
    if (!orphans.length) return null;
    const ahead = orphans.filter((o) => o.cause === "ahead").length;
    const removed = orphans.length - ahead;
    return (
        <div className="border-t border-gray-700 text-xs shrink-0" data-testid="asset-orphans">
            <button
                type="button"
                className="w-full text-left px-2 py-0.5 text-amber-200 hover:bg-gray-700"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                title="Published subjects that no loaded hierarchy places"
            >
                {open ? "▼" : "▶"} Not in this tree: {[ahead && `${ahead} ahead`, removed && `${removed} removed`].filter(Boolean).join(" · ")}
            </button>
            {open && (
                <div className="max-h-32 overflow-auto">
                    {(["ahead", "removed"] as const).map((cause) =>
                        orphans
                            .filter((o) => o.cause === cause)
                            .map((o) => (
                                <button
                                    key={o.id}
                                    type="button"
                                    onClick={() => onSelect(o.id)}
                                    className={`flex w-full items-center gap-1 text-left pl-5 pr-2 py-0.5 whitespace-nowrap ${
                                        selected === o.id ? "bg-blue-700" : "hover:bg-gray-700"
                                    }`}
                                    title={orphanSentence(o, formatRevision)}
                                >
                                    <span className="shrink-0 max-w-[55%] truncate text-white">{o.id}</span>
                                    <span className="min-w-0 truncate text-gray-400">
                                        {orphanHeading(o.cause).toLowerCase()} · {formatRevision(o.revision)}
                                    </span>
                                </button>
                            )),
                    )}
                </div>
            )}
        </div>
    );
};

/** Load / reveal / unload for one row and ONE provider's claim on it. */
interface AssetLoadControl {
    /** The row it was offered for. */
    rowId: string;
    badge: RowBadge;
    provider: string;
    loaded: boolean;
    root: TreeNodeData | null;
    busy: boolean;
    error: string | null;
    /** What the build said it could not draw, once loaded: members of the node's source that are
     *  not in the model, and why -- the build summary's `warnings`. Empty when it drew them all. */
    notes: readonly string[];
    /** The network half of a load -- the delivery claim, and the build when it needs one -- without
     *  touching the scene. A bulk load starts this for the next nodes while the current one loads. */
    prepare: () => Promise<PreparedNode>;
    /** Resolves when the model is in the scene, or the attempt failed (its error is then in the
     *  store). ``prepared``: a `prepare()` already under way, so it is not started twice. */
    load: (prepared?: Promise<PreparedNode>) => Promise<void>;
    unload: () => void;
    reveal: () => void;
}

/** Load / reveal / unload for one row, one control per provider with a deliverable claim on it,
 *  driven off the same `RowBadge`s the tree's dots read (`rowFacts().claims`) -- a `solid` or
 *  `ghost` claim is deliverable; `below` is not (the content is further DOWN the tree, so there
 *  is nothing at or above this row to load).
 *
 *  One hook for the detail's bottom row and the row's context menu, so the two can never disagree
 *  about what loading this row means. Load state is keyed per row AND provider: two providers'
 *  geometry for one node are two loads, with two scene names (`assetSourceName` carries the
 *  provider), and either can be on screen without the other.
 *
 *  For several rows (a selection), every row's controls in row order, each scene model ONCE: rows
 *  covered by the same publish above them would otherwise each load the same model. */
function useAssetLoads(view: AssetView, ids: readonly string[], scope: string): AssetLoadControl[] {
    const { useAssetBrowserStore, useModelState, useTreeViewStore } = useViewerStores();
    const loadBusy = useAssetBrowserStore((s) => s.loadBusy);
    const loadErrors = useAssetBrowserStore((s) => s.loadErrors);
    const loaded = useAssetBrowserStore((s) => s.loaded);
    const liveSourceNames = useModelState((s) => s.loadedSourceNames);
    const treeData = useTreeViewStore((s) => s.treeData);

    // Keep the `loaded` mirror honest against the scene's own truth: a model
    // unloaded from the Files tab (or anywhere else) must stop showing as
    // loaded here on the very next render, not linger until some unrelated
    // asset-browser action happens to touch the store.
    useEffect(() => {
        useAssetBrowserStore.getState().reconcileLoaded(liveSourceNames);
    }, [liveSourceNames, useAssetBrowserStore]);

    const out: AssetLoadControl[] = [];
    const seen = new Set<string>();
    for (const id of ids) for (const badge of rowFacts(view, id)?.claims ?? []) {
        if (badge.weight === "below") continue;
        const ref = refForBadge(view, id, badge);
        if (!ref) continue;
        const key = loadKey(id, badge.provider);
        const sourceName = assetSourceName(ref);
        if (seen.has(sourceName)) continue;
        seen.add(sourceName);
        const asset = loaded.find((a) => a.sourceName === sourceName);
        const loadedHere = !!asset;
        const root = loadedHere ? loadedTreeRoot(treeData, sourceName) : null;
        const deps = () => realDeliveryDeps((name) => useModelState.getState().loadedSourceNames.has(name));
        const prepare = async (): Promise<PreparedNode> => {
            // The claim's own provider in the path: the server then reads THAT provider's
            // manifest for the subject, not whichever provider published it last.
            const wireClaim = await assetsApi.getAssetDelivery(scope, ref.provider, ref.collection, ref.subject, {
                revision: ref.revision,
            });
            return prepareNode(deps(), scope, ref, parseDeliveryClaim(wireClaim));
        };
        out.push({
            rowId: id,
            badge,
            provider: badge.provider,
            loaded: loadedHere,
            root,
            busy: loadBusy.has(key),
            error: loadErrors.get(key) ?? null,
            notes: asset?.warnings ?? [],
            // The work lives in the store, not in the caller: a context menu closes
            // the moment its item is clicked, and the load must outlive it.
            prepare,
            load: (prepared) => {
                useAssetBrowserStore.getState().beginLoad(key);
                return (async () => {
                    try {
                        // The row's label names the model in the scene; with two providers' geometry
                        // for one node side by side, the provider tells the two rows apart.
                        const facts = rowFacts(view, id);
                        const label = facts?.node.label ?? id;
                        const several = (facts?.claims ?? []).filter((b) => b.weight !== "below").length > 1;
                        const asset = await loadPrepared(deps(), ref, prepared ?? prepare(), several ? `${label} · ${badge.provider}` : label);
                        useAssetBrowserStore.getState().endLoad(key, asset);
                        requestRender();
                    } catch (e) {
                        useAssetBrowserStore.getState().failLoad(key, e instanceof Error ? e.message : String(e));
                    }
                })();
            },
            unload: () => {
                makePluginContextStandalone(OWNER).scene.unloadModel(sourceName);
                requestRender();
                // Optimistic: the effect above will re-confirm against
                // `loadedSourceNames` on the next render regardless.
                useAssetBrowserStore.getState().reconcileLoaded(new Set([...liveSourceNames].filter((n) => n !== sourceName)));
            },
            reveal: () => {
                if (root) void selectTreeNode(root);
            },
        });
    }

    // Start the loads an on-demand request was waiting for, once its publish has given the row a
    // claim. `take` makes the start exactly-once across every mounted instance of this hook.
    const pending = usePendingLoads((s) => s.keys);
    useEffect(() => {
        if (!pending.size) return;
        for (const control of out) {
            if (control.loaded || control.busy) continue;
            if (usePendingLoads.getState().take(loadKey(control.rowId, control.provider))) void control.load();
        }
        // `out` is rebuilt every render; what matters is the claims it carries and the pending set.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [pending, out.map((c) => loadKey(c.rowId, c.provider)).join("|")]);
    return out;
}

/** The store's load-state key: a row and a provider, since each provider's claim loads alone. */
function loadKey(id: string, provider: string): string {
    return `${id}\u0000${provider}`;
}

/** Loads waiting on an on-demand request: when the request publishes and the tab re-reads, the
 *  row's new claim for that provider is loaded by whichever `useAssetLoads` sees it first.
 *  Module-level rather than component state because the request outlives the menu or detail that
 *  started it, and the claim arrives in a later render than the click. */
const usePendingLoads = create<{
    keys: ReadonlySet<string>;
    add: (keys: readonly string[]) => void;
    drop: (keys: readonly string[]) => void;
    /** Remove `key` and say whether it was there -- so exactly one caller starts the load. */
    take: (key: string) => boolean;
}>((set, get) => ({
    keys: new Set(),
    add: (keys) => set((s) => ({ keys: new Set([...s.keys, ...keys]) })),
    drop: (keys) => set((s) => ({ keys: new Set([...s.keys].filter((k) => !keys.includes(k))) })),
    take: (key) => {
        if (!get().keys.has(key)) return false;
        set((s) => ({ keys: new Set([...s.keys].filter((k) => k !== key)) }));
        return true;
    },
}));

/** "Load into scene" for a provider that has published nothing at or above these rows yet, but
 *  can be asked for them quickly (`on_demand`). One per such provider. */
interface RequestLoadControl {
    provider: string;
    busy: boolean;
    blocked: string | null;
    /** Confirm with the user, request, and load what arrives. */
    run: () => void;
}

/** The on-demand request-and-load controls for `ids`: every on-demand provider with no deliverable
 *  claim on any of them. A provider that already has a claim loads it the ordinary way. */
function requestLoadsFor(
    view: AssetView,
    ids: readonly string[],
    loads: readonly AssetLoadControl[],
    requests: readonly NodeRequestControl[],
): RequestLoadControl[] {
    const claimed = new Set(loads.map((l) => l.provider));
    return requests
        .filter((r) => r.onDemand && !claimed.has(r.provider))
        .map((r) => ({
            provider: r.provider,
            busy: r.busy,
            blocked: r.blocked,
            run: () => {
                const what =
                    ids.length === 1 ? `"${rowFacts(view, ids[0])?.node.label ?? ids[0]}"` : `these ${ids.length} nodes`;
                const ok = window.confirm(
                    `${what} ${ids.length === 1 ? "has" : "have"} no ${r.provider} geometry in this scope yet.\n\n` +
                        `Request ${ids.length === 1 ? "it" : "them"} from ${r.provider} now? The geometry is fetched once, ` +
                        "published here so it is cached for everyone in the scope, and then loaded into the scene.",
                );
                if (!ok) return;
                const keys = ids.map((id) => loadKey(id, r.provider));
                usePendingLoads.getState().add(keys);
                // A failed request is reported where every request reports (its error under the
                // detail's request controls); the loads it would have started are dropped with it.
                void r.run().then((done) => {
                    if (!done) usePendingLoads.getState().drop(keys);
                });
            },
        }));
}

/** Asking the provider for one node's geometry (`asset_node_request`), as the tab tracks it. Null
 *  where the node's provider declares no such request. */
interface NodeRequestControl {
    /** The provider asked, and the one the result is published under. */
    provider: string;
    label: string;
    busy: boolean;
    stage: string | null;
    error: string | null;
    /** After a request that found its source unchanged: which existing publish still covers it. */
    note: string | null;
    /** Why the request cannot be made by this user, or null. */
    blocked: string | null;
    /** The provider declared the request quick enough to run as part of a load (`on_demand`). */
    onDemand: boolean;
    /** Resolves true when every batch published (or was already up to date) and the tab re-read. */
    run: () => Promise<boolean>;
}

const NOTHING_TO_LOAD = "No geometry is published at or above this node yet";
const NOTHING_TO_LOAD_ANY = "No geometry is published at or above any of the selected nodes yet";

/** A selection's load controls, per provider: what is still to load, what is in the scene, what is
 *  loading and what failed. One reading for the detail's buttons and the menu's items. */
interface LoadGroup {
    provider: string;
    toLoad: AssetLoadControl[];
    loaded: AssetLoadControl[];
    busy: number;
    failed: AssetLoadControl[];
}

function loadGroups(controls: readonly AssetLoadControl[]): LoadGroup[] {
    const by = new Map<string, LoadGroup>();
    for (const c of controls) {
        const g = by.get(c.provider) ?? { provider: c.provider, toLoad: [], loaded: [], busy: 0, failed: [] };
        if (c.loaded) g.loaded.push(c);
        else if (c.busy) g.busy += 1;
        else g.toLoad.push(c);
        if (!c.busy && c.error) g.failed.push(c);
        by.set(c.provider, g);
    }
    return [...by.values()];
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Load a selection's models as ONE bulk load of the collection: the camera goes to its loading
 *  view (or frames the first model) and stays there, and the models load one at a time -- several
 *  parsed at once was the main thread held for all of them together. A single model is an
 *  ordinary load, fitted as before. */
async function loadSelection(scope: string, collection: string, controls: readonly AssetLoadControl[]): Promise<void> {
    if (controls.length < 2) {
        await controls[0]?.load();
        return;
    }
    const session = beginBulkLoad(scope, `assets:${collection}`);
    // THE NEXT NODES ARE PREPARED WHILE ONE LOADS. Preparing a node is network and server work --
    // its delivery claim, and for a node not built yet a build job that can take minutes -- and
    // nothing in it touches the scene, so a few run ahead of the loads, which stay one at a time.
    const PREPARE_AHEAD = 3;
    const prepared = new Map<AssetLoadControl, Promise<PreparedNode>>();
    const prepareAhead = (from: number) => {
        for (let j = from; j < Math.min(controls.length, from + 1 + PREPARE_AHEAD); j++) {
            if (prepared.has(controls[j])) continue;
            const p = controls[j].prepare();
            // Settled even if nobody awaits it: its error, if any, surfaces in that node's own load.
            p.catch(() => undefined);
            prepared.set(controls[j], p);
        }
    };
    try {
        for (let i = 0; i < controls.length; i++) {
            prepareAhead(i);
            await controls[i].load(prepared.get(controls[i]));
            prepared.delete(controls[i]);
        }
    } finally {
        endBulkLoad(session);
    }
}

/** How to ask one provider for geometry for some rows -- `nodeRequestsFor`, narrowed to what the set
 *  load needs. `null` where the provider declares no node request. */
type RequestMissing = (provider: string, ids: readonly string[]) => { blocked: string | null; run: () => Promise<boolean> } | null;

/** "Load set": every member's geometry, from the providers the member chose, as ONE bulk load.
 *
 *  The same controls a selection's load uses (`useAssetLoads`), so what loads, how it is named in
 *  the scene and how it reports a failure are the tree's own.
 *
 *  ASKED FIRST, NOT DISCOVERED BY FAILING. Each (member, provider) pair is checked against that
 *  provider's published geometry before anything loads. A provider that published only the TREE
 *  has a claim on every row but nothing to load, and attempting it fails once per member. When any
 *  pair has no geometry, the load stops at a prompt: request the missing ones (and load each as its
 *  publish arrives), load only what is there, or cancel. */
const SetLoad: React.FC<{ view: AssetView; set: TreeSet; scope: string; requestMissing?: RequestMissing }> = ({
    view,
    set,
    scope,
    requestMissing,
}) => {
    const { useAssetBrowserStore } = useViewerStores();
    const rollup = useAssetBrowserStore((s) => s.geometryRollup);
    const [asking, setAsking] = useState(false);
    const present = set.members.filter((m) => view.hierarchy.byId.has(m.id));
    const controls = useAssetLoads(
        view,
        present.map((m) => m.id),
        scope,
    );
    // One geometry index per provider, for every provider a member could have chosen.
    const indexes = useMemo(
        () => new Map(view.contentProviders.map((p) => [p, geometryIndex(view, p, rollup)] as const)),
        [view, rollup],
    );
    const loadable = (id: string, provider: string) => {
        const idx = indexes.get(provider);
        return !!idx && rowLoadable(view, idx, id);
    };
    const byId = new Map(present.map((m) => [m.id, m]));
    const wanted = controls.filter((c) => {
        const m = byId.get(c.rowId);
        return !!m && loadsProvider(m, c.provider) && loadable(c.rowId, c.provider);
    });
    const toLoad = wanted.filter((c) => !c.loaded && !c.busy);
    const loaded = wanted.filter((c) => c.loaded);
    const busy = wanted.filter((c) => c.busy).length;
    const failed = wanted.filter((c) => !c.busy && c.error);
    // Every pair the set asks for that has no geometry to load, by provider -- split into what was
    // never requested and what WAS: the provider published that very node with nothing to load
    // (an empty site, a kind it cannot draw). Asking again would publish the same nothing.
    const missing = new Map<string, { id: string; label: string }[]>();
    const empty = new Map<string, { id: string; label: string }[]>();
    for (const m of present) {
        for (const p of m.providers ?? view.contentProviders) {
            if (loadable(m.id, p)) continue;
            const own = view.resolution.subjects.get(m.id)?.byProvider.get(p);
            const into = own?.manifest ? empty : missing;
            const list = into.get(p) ?? [];
            list.push({ id: m.id, label: m.label });
            into.set(p, list);
        }
    }
    const emptyCount = [...empty.values()].reduce((n, l) => n + l.length, 0);
    const missingCount = [...missing.values()].reduce((n, l) => n + l.length, 0);
    const requests = [...missing.entries()].map(([provider, rows]) => ({
        provider,
        rows,
        request: requestMissing?.(provider, rows.map((r) => r.id)) ?? null,
    }));
    const requestable = requests.filter((r) => r.request && !r.request.blocked);
    const requestableCount = requestable.reduce((n, r) => n + r.rows.length, 0);

    const loadAvailable = () => void loadSelection(scope, view.collection, toLoad);
    const requestAndLoad = () => {
        setAsking(false);
        for (const r of requestable) {
            const keys = r.rows.map((row) => loadKey(row.id, r.provider));
            // Loaded by whichever `useAssetLoads` sees the claim first, once the publish lands.
            usePendingLoads.getState().add(keys);
            void r.request!.run().then((done) => {
                if (!done) usePendingLoads.getState().drop(keys);
            });
        }
        if (toLoad.length) loadAvailable();
    };

    return (
        <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-1.5">
                <button
                    type="button"
                    className={BTN_PRIMARY}
                    disabled={!toLoad.length && !missingCount}
                    title={toLoad.length ? toLoad.map((c) => `${rowFacts(view, c.rowId)?.node.label ?? c.rowId} · ${c.provider}`).join("\n") : "Nothing left to load"}
                    onClick={() => (missingCount ? setAsking(true) : loadAvailable())}
                >
                    {busy ? `Loading… (${busy} left)` : toLoad.length || missingCount ? `Load set (${plural(toLoad.length + missingCount, "model")})` : "Set loaded"}
                </button>
                {loaded.length > 0 && (
                    <button type="button" className={BTN_SECONDARY} onClick={() => loaded.forEach((c) => c.unload())}>
                        Unload ({loaded.length})
                    </button>
                )}
                {failed.length > 0 && (
                    <span className="text-red-300" title={failed.map((c) => `${c.rowId} · ${c.provider}: ${c.error}`).join("\n")}>
                        {failed.length} failed
                    </span>
                )}
                {missingCount > 0 && !asking && (
                    <span className="text-amber-300" title={requests.map((r) => `${r.provider}: ${r.rows.map((x) => x.label).join(", ")}`).join("\n")}>
                        {missingCount} not requested yet
                    </span>
                )}
                {emptyCount > 0 && (
                    <span
                        className="text-gray-400"
                        title={[...empty.entries()].map(([p, rows]) => `${p}: ${rows.map((x) => x.label).join(", ")}`).join("\n")}
                    >
                        {emptyCount} with nothing to draw
                    </span>
                )}
            </div>
            {asking && (
                <div role="alertdialog" aria-label="Missing geometry" className="rounded-md border border-amber-700/70 bg-amber-950/40 p-2 space-y-1.5 text-xs">
                    <div className="text-amber-100">
                        {missingCount} of {toLoad.length + missingCount} have no geometry published yet:
                    </div>
                    <ul className="space-y-0.5">
                        {requests.map((r) => (
                            <li key={r.provider} className="text-gray-200" title={r.rows.map((x) => x.label).join("\n")}>
                                <span className="font-mono">{r.provider}</span>: {r.rows.length}
                                <span className="text-gray-400">
                                    {" — "}
                                    {!r.request ? "this provider cannot be asked for it" : r.request.blocked ? r.request.blocked : "can be requested"}
                                </span>
                            </li>
                        ))}
                    </ul>
                    {emptyCount > 0 && (
                        <div className="text-gray-400">
                            {emptyCount} more were requested before and have nothing to draw; they are left out.
                        </div>
                    )}
                    <div className="flex flex-wrap gap-1.5">
                        <button
                            type="button"
                            className={BTN_PRIMARY}
                            disabled={!requestableCount}
                            title="Ask the providers for the missing geometry, load what is there now, and load each requested one as its publish arrives"
                            onClick={requestAndLoad}
                        >
                            Request {requestableCount} and load
                        </button>
                        <button
                            type="button"
                            className={BTN_SECONDARY}
                            disabled={!toLoad.length}
                            onClick={() => {
                                setAsking(false);
                                loadAvailable();
                            }}
                        >
                            Load the {toLoad.length} available
                        </button>
                        <button type="button" className={BTN_QUIET} onClick={() => setAsking(false)}>
                            Cancel
                        </button>
                    </div>
                </div>
            )}
        </div>
    );
};

const LoadControls: React.FC<{
    view: AssetView;
    ids: readonly string[];
    scope: string;
    requests?: readonly NodeRequestControl[];
}> = ({ view, ids, scope, requests = [] }) => {
    const controls = useAssetLoads(view, ids, scope);
    const onDemand = requestLoadsFor(view, ids, controls, requests);
    const named = controls.length + onDemand.length > 1;
    const requestLoads = onDemand.map((r) => <RequestLoad key={`request:${r.provider}`} control={r} named={named} />);
    if (!controls.length && onDemand.length) {
        return <div className="flex items-center gap-2 min-w-0 flex-wrap">{requestLoads}</div>;
    }
    if (!controls.length) {
        // Drawn, and disabled: a missing button reads as a layout glitch, a greyed one as "not yet".
        return (
            <button type="button" disabled className={`${BTN_PRIMARY} shrink-0`} title={ids.length > 1 ? NOTHING_TO_LOAD_ANY : NOTHING_TO_LOAD}>
                Load into scene
            </button>
        );
    }
    if (ids.length > 1) {
        return (
            <div className="flex items-center gap-2 min-w-0 flex-wrap">
                <BulkLoads groups={loadGroups(controls)} scope={scope} collection={view.collection} />
                {requestLoads}
            </div>
        );
    }
    // One provider: the plain button. Several: one per provider, each naming it -- which
    // geometry lands in the scene is the user's choice, and the two can be compared side by side.
    return (
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
            {controls.map((control) => (
                <SingleLoad key={control.provider} control={control} named={named} />
            ))}
            {requestLoads}
        </div>
    );
};

/** "Load into scene" from a provider that has not published this node here yet: asks first, then
 *  requests, publishes and loads (`requestLoadsFor`). */
const RequestLoad: React.FC<{ control: RequestLoadControl; named: boolean }> = ({ control, named }) => (
    <button
        type="button"
        disabled={control.busy || !!control.blocked}
        className={`${BTN_PRIMARY} shrink-0`}
        title={control.blocked ?? `Not published here yet — requests it from ${control.provider} first, then loads it`}
        onClick={control.run}
    >
        {control.busy ? `Requesting${named ? ` · ${control.provider}` : ""}…` : named ? `Load · ${control.provider}` : "Load into scene"}
    </button>
);

/** A selection's loads, one set of controls per provider: load what is not in the scene yet, unload
 *  what is. Each model still loads on its own -- this only starts them together. */
const BulkLoads: React.FC<{ groups: readonly LoadGroup[]; scope: string; collection: string }> = ({ groups, scope, collection }) => {
    const named = groups.length > 1;
    return (
        <div className="flex items-center gap-2 min-w-0 flex-wrap">
            {groups.map((g) => (
                <div key={g.provider} className="flex items-center gap-2 min-w-0">
                    <button
                        type="button"
                        disabled={!g.toLoad.length}
                        className={`${BTN_PRIMARY} shrink-0`}
                        title={`Load ${plural(g.toLoad.length, "model")} from ${g.provider}`}
                        onClick={() => void loadSelection(scope, collection, g.toLoad)}
                    >
                        {g.toLoad.length ? `Load ${g.toLoad.length}` : "All loaded"}
                        {named ? ` · ${g.provider}` : ""}
                    </button>
                    {g.busy > 0 && <span className="text-gray-400 shrink-0">loading {g.busy}…</span>}
                    {g.loaded.length > 0 && (
                        <button
                            type="button"
                            className="text-gray-300 hover:text-white shrink-0"
                            title={`Unload the ${plural(g.loaded.length, "model")} from ${g.provider} in the scene`}
                            onClick={() => g.loaded.forEach((c) => c.unload())}
                        >
                            unload {g.loaded.length}
                        </button>
                    )}
                    {g.failed.length > 0 && (
                        <span className="text-red-300 truncate" title={g.failed.map((c) => `${c.rowId}: ${c.error}`).join("\n")}>
                            {g.failed.length} failed
                        </span>
                    )}
                </div>
            ))}
        </div>
    );
};

const SingleLoad: React.FC<{ control: AssetLoadControl; named: boolean }> = ({ control, named }) => {
    const { badge, root } = control;
    const via = badge.weight === "ghost" ? ` via ${badge.at}` : "";
    if (control.loaded) {
        return (
            <div className="flex items-center gap-2 min-w-0" title={`${control.provider}${via}`}>
                <span className="text-green-300 truncate">Loaded{named ? ` · ${control.provider}` : via ? ` (${via.trim()})` : ""}</span>
                {control.notes.length > 0 && (
                    // Said where the model is, not left for someone to find missing in it.
                    <span className="shrink-0 text-amber-300 cursor-help" title={control.notes.join("\n")}>
                        incomplete
                    </span>
                )}
                <button
                    type="button"
                    className="text-blue-300 hover:text-white disabled:text-gray-500"
                    disabled={!root}
                    title={root ? "Select this model's root in the Scene tab" : "Not in the scene yet"}
                    onClick={control.reveal}
                >
                    reveal
                </button>
                <button type="button" className="text-gray-300 hover:text-white" onClick={control.unload}>
                    unload
                </button>
            </div>
        );
    }
    return (
        <div className="flex items-center gap-2 min-w-0">
            <button
                type="button"
                disabled={control.busy}
                className={`${BTN_PRIMARY} shrink-0`}
                title={`From ${control.provider}${via} @ ${formatRevision(badge.revision)}`}
                onClick={() => void control.load()}
            >
                {control.busy ? "Loading…" : named ? `Load · ${control.provider}` : "Load into scene"}
            </button>
            {control.error && (
                <span className="text-red-300 truncate" title={control.error}>
                    {control.error}
                </span>
            )}
        </div>
    );
};

/** What the selected node IS -- fetched when it is selected, never before.
 *
 *  Attributes are per-node and most nodes are never selected, so carrying them in the spine would
 *  pay for the whole tree to answer for a handful of rows. This fetches one node, in the
 *  background, and shows nothing at all while it is in flight: a spinner over two lines of
 *  properties is more movement than the information is worth.
 *
 *  `subject` is the covering publish (`badge.at`), which is this row for a node published in its
 *  own right and an ancestor for one covered from above. Without it a covered node asks for a
 *  manifest that does not exist and gets the "nothing recorded" answer while its properties sit in
 *  its ancestor's document.
 *
 *  Absence is silence. A provider that publishes no attributes, a document that does not mention
 *  this node and an unpublished node all render nothing -- a caller asking what something is
 *  cannot act differently on those, and a row that said "no properties" would imply somebody
 *  checked. A real FAILURE is shown, because that is not absence. */
const Attributes: React.FC<{ scope: string; provider: string; collection: string; node: string; subject: string | null }> = ({
    scope,
    provider,
    collection,
    node,
    subject,
}) => {
    const [state, setState] = React.useState<{ kind: "none" } | { kind: "ok"; body: WireNodeAttributes } | { kind: "error"; message: string }>({ kind: "none" });

    useEffect(() => {
        let live = true;
        setState({ kind: "none" });
        fetchAssetAttributes(scope as ScopeUrl, provider, collection, node, subject ? { subject } : undefined)
            .then((body) => {
                if (!live) return;
                setState(body ? { kind: "ok", body } : { kind: "none" });
            })
            .catch((e) => {
                if (live) setState({ kind: "error", message: e instanceof Error ? e.message : String(e) });
            });
        // A selection that moves before the answer lands must not paint the old node's
        // properties under the new node's name.
        return () => {
            live = false;
        };
    }, [scope, provider, collection, node, subject]);

    if (state.kind === "none") return null;
    if (state.kind === "error") {
        return (
            <div className="mt-1 text-red-300 break-words" data-testid="asset-attributes-error">
                properties unavailable: {state.message}
            </div>
        );
    }

    const { own, groups, quantities } = state.body;
    const sections: [string, Readonly<Record<string, unknown>>][] = [
        ...Object.entries(groups),
        ...Object.entries(quantities),
    ];
    return (
        <div className="mt-1 border-t border-gray-800 pt-1" data-testid="asset-attributes">
            {Object.entries(own).map(([k, v]) => (
                <div key={k} className="flex gap-2">
                    <span className="text-gray-400 w-20 shrink-0">{k}</span>
                    <span className="min-w-0 break-words">{String(v)}</span>
                </div>
            ))}
            {sections.map(([name, props]) => (
                <div key={name} className="mt-1">
                    <div className="text-gray-400">{name}</div>
                    {Object.entries(props).map(([k, v]) => (
                        <div key={k} className="flex gap-2 pl-2">
                            <span className="text-gray-400 w-20 shrink-0 truncate" title={k}>
                                {k}
                            </span>
                            <span className="min-w-0 break-words">{String(v)}</span>
                        </div>
                    ))}
                </div>
            ))}
        </div>
    );
};

/** Check a published node for joints, and take the answer where joints are already shown.
 *
 *  A context-menu action, not a button in the detail: it is an occasional analysis, and the
 *  detail's bottom row is kept for what a row is FOR -- putting it in the scene.
 *
 *  WHY THE RESULT IS NOT HERE. The Clashes panel already renders a result -- groups, types, the
 *  detail hand-off, the producer filter -- and a second joints table in this tab would be a second
 *  implementation of the same reading, free to disagree with it. So this switches to that panel
 *  and runs the check there; the panel shows it running, and shows a failure, itself. That is also
 *  where a user who ran one from a FILE ends up. The two ways in converge on one surface.
 *
 *  OFFERED ONLY FOR A NODE SOMETHING IS PUBLISHED AT OR ABOVE. A check reads the published
 *  source, so a row with no covering publish has nothing to read -- and an item that enqueued a
 *  job which 404s is worse than no item.
 *
 *  THE SUBJECT IS THE COVERING PUBLISH, not always this row: a leaf published under a root has no
 *  manifest of its own, and the badge already resolved which one speaks for it.
 */
function useJointsCheck(view: AssetView, id: string, scope: string) {
    const busy = useClashCheckStore((s) => s.busy);
    const setAssetTarget = useClashCheckStore((s) => s.setAssetTarget);
    const runCheck = useClashCheckStore((s) => s.runCheck);
    const setMode = useSceneInfoStore((s) => s.setMode);
    const setShowSceneInfoBox = useSceneInfoStore((s) => s.setShowSceneInfoBox);

    const badge: RowBadge | null = rowFacts(view, id)?.badge ?? null;
    if (!badge) return null;
    const run = () => {
        setAssetTarget(`${view.collection} / ${id}`, {
            kind: "node",
            collection: view.collection,
            subject: badge.at,
            revision: badge.revision,
            node: id,
            provider: badge.provider,
            collectionProviders: view.contentProviders,
        });
        setMode("clashes");
        setShowSceneInfoBox(true);
        void runCheck(scope);
    };
    return { busy, run };
}

const REQUEST_TITLE = "Ask a provider for this node's geometry, then publish it here";

/** Which providers to ask, as checkboxes -- the several providers that can be asked for one node
 *  each publish their own geometry for it, and asking all of them is rarely what is wanted. Opened
 *  from the detail's Request button or the row menu; portalled, like the menu, so it clears the
 *  drawer's overflow. */
const RequestPicker: React.FC<{
    requests: readonly NodeRequestControl[];
    anchor: { x: number; y: number; above?: boolean };
    onClose: () => void;
}> = ({ requests, anchor, onClose }) => {
    const ref = React.useRef<HTMLDivElement>(null);
    const [picked, setPicked] = useState<ReadonlySet<string>>(
        () => new Set(requests.filter((r) => !r.blocked && !r.busy).map((r) => r.provider)),
    );
    const [pos, setPos] = useState<React.CSSProperties>({ left: anchor.x, top: anchor.y, visibility: "hidden" });
    React.useLayoutEffect(() => {
        const el = ref.current;
        const w = el?.offsetWidth ?? 240;
        const h = el?.offsetHeight ?? 160;
        const left = Math.max(8, Math.min(anchor.x, window.innerWidth - w - 8));
        const top = anchor.above ? Math.max(8, anchor.y - h - 6) : Math.max(8, Math.min(anchor.y, window.innerHeight - h - 8));
        setPos({ left, top });
        const outside = (e: Event) => {
            if (ref.current && !ref.current.contains(e.target as Node)) onClose();
        };
        const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
        document.addEventListener("mousedown", outside);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", outside);
            document.removeEventListener("keydown", onKey);
        };
    }, [anchor.x, anchor.y, anchor.above, onClose]);

    const toggle = (p: string) =>
        setPicked((cur) => {
            const next = new Set(cur);
            if (!next.delete(p)) next.add(p);
            return next;
        });
    const chosen = requests.filter((r) => picked.has(r.provider) && !r.blocked && !r.busy);
    return createPortal(
        <div
            ref={ref}
            role="dialog"
            aria-label="Request geometry"
            className="fixed z-[70] w-64 rounded-md border border-gray-700 bg-gray-800 shadow-lg text-xs text-gray-100"
            style={pos}
            onContextMenu={(e) => e.preventDefault()}
        >
            <div className="px-3 py-2 border-b border-gray-700 text-gray-400">Request geometry from</div>
            <div className="py-1">
                {requests.map((r) => (
                    <label
                        key={r.provider}
                        className={`flex items-start gap-2 px-3 py-1.5 ${r.blocked || r.busy ? "opacity-50" : "hover:bg-gray-700 cursor-pointer"}`}
                        title={r.blocked ?? (r.busy ? "Already requested; running" : REQUEST_TITLE)}
                    >
                        <input
                            type="checkbox"
                            className="mt-0.5"
                            disabled={!!r.blocked || r.busy}
                            checked={picked.has(r.provider) && !r.blocked}
                            onChange={() => toggle(r.provider)}
                        />
                        <span className="min-w-0">
                            <span className="block font-medium truncate">{r.provider}</span>
                            <span className="block text-gray-400 truncate">{r.busy ? r.stage ?? "requesting…" : r.label}</span>
                        </span>
                    </label>
                ))}
            </div>
            <div className="flex justify-end gap-2 px-3 py-2 border-t border-gray-700">
                <button type="button" className={BTN_QUIET} onClick={onClose}>
                    Cancel
                </button>
                <button
                    type="button"
                    className={BTN_PRIMARY}
                    disabled={!chosen.length}
                    onClick={() => {
                        for (const r of chosen) r.run();
                        onClose();
                    }}
                >
                    Request{chosen.length > 1 ? ` (${chosen.length})` : ""}
                </button>
            </div>
        </div>,
        document.body,
    );
};

/** The request button and each running request's progress, for the detail's bottom row. One
 *  provider: the button asks it directly. Several: it opens the picker. */
const RequestControls: React.FC<{ requests: readonly NodeRequestControl[] }> = ({ requests }) => {
    const [pickerAt, setPickerAt] = useState<{ x: number; y: number; above: boolean } | null>(null);
    const single = requests.length === 1 ? requests[0] : null;
    const busy = requests.filter((r) => r.busy);
    const failed = requests.filter((r) => !r.busy && r.error);
    const allBlocked = requests.every((r) => !!r.blocked);
    return (
        <div className="flex items-center gap-2 min-w-0">
            <button
                type="button"
                className={`${BTN_SECONDARY} shrink-0`}
                disabled={single ? single.busy || !!single.blocked : allBlocked}
                title={single ? (single.blocked ?? REQUEST_TITLE) : REQUEST_TITLE}
                onClick={(e) => {
                    if (single) return single.run();
                    const r = e.currentTarget.getBoundingClientRect();
                    setPickerAt({ x: r.left, y: r.top, above: true });
                }}
            >
                {single ? (single.busy ? "Requesting…" : single.label) : "Request geometry…"}
            </button>
            {busy.length > 0 && (
                <span className="text-gray-400 truncate" title={busy.map((r) => `${r.provider}: ${r.stage ?? "requesting"}`).join("\n")}>
                    {busy.length === 1 ? `${busy[0].provider}: ${busy[0].stage ?? "requesting…"}` : `${busy.length} requests running`}
                </span>
            )}
            {busy.length === 0 && failed.length > 0 && (
                <span className="text-red-300 truncate" title={failed.map((r) => `${r.provider}: ${r.error}`).join("\n")}>
                    {failed.length === 1 && requests.length === 1 ? failed[0].error : `${failed.map((r) => r.provider).join(", ")} failed`}
                </span>
            )}
            {busy.length === 0 && failed.length === 0 && requests.some((r) => r.note) && (
                <span
                    className="text-gray-400 truncate"
                    title={requests
                        .filter((r) => r.note)
                        .map((r) => `${r.provider}: ${r.note}`)
                        .join("\n")}
                >
                    {requests
                        .filter((r) => r.note)
                        .map((r) => (requests.length > 1 ? `${r.provider} ${r.note}` : r.note))
                        .join("; ")}
                </span>
            )}
            {pickerAt && <RequestPicker requests={requests} anchor={pickerAt} onClose={() => setPickerAt(null)} />}
        </div>
    );
};

/** Right-click menu for a tree row -- or for the selection it is part of (`ids`, its topmost rows):
 *  every action then applies to all of them. */
const AssetRowMenu: React.FC<{
    view: AssetView;
    /** The row right-clicked. */
    id: string;
    /** What the actions apply to: `[id]`, or the selection it is part of. */
    ids: readonly string[];
    scope: string;
    x: number;
    y: number;
    requests: readonly NodeRequestControl[];
    /** Several requestable providers: the menu item opens the picker here instead of asking one. */
    onPickRequests: (x: number, y: number) => void;
    /** The out-of-scope switch for these rows, or null where it does not apply. */
    scopeItem: KebabMenuItem | null;
    /** "Add to set" / "Remove from set", one per set of the collection. */
    setItems: readonly KebabMenuItem[];
    onClose: () => void;
}> = ({ view, id, ids, scope, x, y, requests, onPickRequests, scopeItem, setItems, onClose }) => {
    const many = ids.length > 1;
    const loads = useAssetLoads(view, ids, scope);
    const onDemand = requestLoadsFor(view, ids, loads, requests);
    const joints = useJointsCheck(view, id, scope);
    const items: KebabMenuItem[] = [];
    // A provider that can be asked on demand loads from here too: confirm, request, load.
    for (const r of onDemand) {
        const from = loads.length + onDemand.length > 1 ? ` · ${r.provider}` : "";
        items.push({
            key: `request-load:${r.provider}`,
            label: r.busy ? `Requesting…${from}` : `Load into scene${from}`,
            disabled: r.busy || !!r.blocked,
            title: r.blocked ?? `Not published here yet — requests it from ${r.provider} first, then loads it`,
            onClick: r.run,
        });
    }
    if (!loads.length && !onDemand.length) {
        items.push({ key: "load", label: "Load into scene", disabled: true, onClick: () => {}, title: many ? NOTHING_TO_LOAD_ANY : NOTHING_TO_LOAD });
    }
    if (many) {
        const groups = loadGroups(loads);
        for (const g of groups) {
            const from = groups.length > 1 ? ` · ${g.provider}` : "";
            items.push({
                key: `load:${g.provider}`,
                label: g.toLoad.length ? `Load ${plural(g.toLoad.length, "model")} into scene${from}` : `All loaded${from}`,
                disabled: !g.toLoad.length,
                title: g.busy ? `${g.busy} still loading` : undefined,
                onClick: () => void loadSelection(scope, view.collection, g.toLoad),
            });
            if (g.loaded.length) {
                items.push({
                    key: `unload:${g.provider}`,
                    label: `Unload ${plural(g.loaded.length, "model")} from scene${from}`,
                    onClick: () => g.loaded.forEach((c) => c.unload()),
                });
            }
        }
    }
    const named = loads.length > 1;
    for (const load of many ? [] : loads) {
        const from = named ? ` · ${load.provider}` : "";
        if (load.loaded) {
            items.push({ key: `reveal:${load.provider}`, label: `Reveal in Scene${from}`, disabled: !load.root, onClick: load.reveal });
            items.push({ key: `unload:${load.provider}`, label: `Unload from scene${from}`, onClick: load.unload });
        } else {
            items.push({
                key: `load:${load.provider}`,
                label: load.busy ? `Loading…${from}` : `Load into scene${from}`,
                disabled: load.busy,
                title: `From ${load.provider} @ ${formatRevision(load.badge.revision)}`,
                onClick: () => void load.load(),
            });
        }
    }
    if (requests.length === 1) {
        const request = requests[0];
        items.push({
            key: "request",
            label: request.busy ? "Requesting…" : request.label,
            disabled: request.busy || !!request.blocked,
            title: request.blocked ?? REQUEST_TITLE,
            onClick: request.run,
        });
    } else if (requests.length > 1) {
        items.push({
            key: "request",
            label: many ? `Request geometry for ${ids.length}…` : "Request geometry…",
            disabled: requests.every((r) => !!r.blocked),
            title: REQUEST_TITLE,
            onClick: () => onPickRequests(x, y),
        });
    }
    if (scopeItem) items.push(scopeItem);
    items.push(...setItems);
    // One node's analysis: offered for the row clicked, not for a selection.
    if (joints && !many) {
        items.push({
            key: "joints",
            label: "Check for joints",
            separatorBefore: true,
            disabled: joints.busy,
            title: "Identify the joints in this node's published source",
            onClick: joints.run,
        });
    }
    const label = many ? `${ids.length} selected` : (rowFacts(view, id)?.node.label ?? id);
    return <PositionedMenu items={items} anchor={{ kind: "point", x, y }} onClose={onClose} header={label} />;
};

/** The selected row's detail: facts and properties in a scrolling body, and the row's actions on
 *  a bottom row that never scrolls. The properties are the provider's and can run to dozens of
 *  lines; with the actions inside the scroll, "Load into scene" was pushed out of sight by them. */
const Detail: React.FC<{
    view: AssetView;
    id: string;
    /** What the bottom row's actions apply to: `[id]`, or the selection's topmost rows. The facts
     *  above are always `id`'s -- the focused row. */
    ids: readonly string[];
    scope: string;
    requests: readonly NodeRequestControl[];
    actions?: React.ReactNode;
}> = ({ view, id, ids, scope, requests, actions }) => {
    const facts = rowFacts(view, id);
    const orphan = view.orphans.find((o) => o.id === id);
    const resolved = view.resolution.subjects.get(id);
    const manifest = resolved?.revision.manifest ?? null;
    const lines: [string, React.ReactNode][] = [];
    if (facts) {
        lines.push(["Id", <span className="font-mono text-[11px]">{id}</span>]);
        lines.push(["Provider", <span className="font-mono text-[11px]">{facts.node.provider}</span>]);
        lines.push(["Claim", facts.node.delivery === "none" ? "none" : facts.node.delivery]);
    }
    if (resolved) lines.push(["Published", `${formatRevision(resolved.revision.revision)}${manifest ? ` · ${manifest.delivery}` : ""}`]);
    else if (facts) lines.push(["Published", "not as a subject of its own"]);
    if (facts?.badge && facts.badge.weight !== "solid") {
        lines.push([facts.badge.weight === "ghost" ? "Covered by" : "Content below", `${facts.badge.at} @ ${formatRevision(facts.badge.revision)}`]);
    }
    if (facts?.freshness?.stale) {
        lines.push(["Drawn from", `${formatRevision(facts.freshness.shownAt)} — now resolves to ${formatRevision(facts.freshness.resolvedAt!)}`]);
    }
    if (facts?.drift) {
        lines.push(["Tree", `published against ${formatRevision(facts.drift.publishedAgainst)}; shown from ${formatRevision(facts.drift.shownFrom)}`]);
    }
    // Behind-upstream is its OWN line, never merged into "Drawn from" above:
    // that line is about OUR spine lagging OUR resolution (fixed by Refresh),
    // this one is about the SOURCE moving past what was published (fixed only
    // by a new export). Same sentence for both would say the wrong fix.
    const change = view.changes.byRoot.get(id);
    if (change && change.state === "behind") {
        lines.push([
            "Behind source",
            `changed ${change.lastChangedAt ?? "—"}${change.lastChangedBy ? ` by ${change.lastChangedBy}` : ""} — after this was published; re-export to catch up`,
        ]);
    } else if (change) {
        lines.push(["Source", CHANGE_STATE_LABEL[change.state]]);
    }
    // §Decision 6: two separately-labelled facts, never merged into one
    // "author" -- `publishedBy` is core-stamped and trustworthy, `sourceActor`
    // is merely relayed by the provider from its own source. Absent is the
    // normal case and renders nothing at all, not a placeholder.
    if (facts?.changeRecord?.publishedBy) {
        const a = facts.changeRecord.publishedBy;
        lines.push(["Published by", a.display ? `${a.display} (${a.id})` : a.id]);
    }
    if (facts?.changeRecord?.sourceActor) {
        const a = facts.changeRecord.sourceActor;
        lines.push(["Source says", a.display ? `${a.display} (${a.id})` : a.id]);
    }
    if (orphan) lines.push(["Not in tree", orphanSentence(orphan, formatRevision)]);
    const err = view.manifestErrors.get(id);
    if (err) lines.push(["Manifest", err]);
    return (
        <div className="border-t border-gray-700/70 bg-gray-900/40 text-xs text-gray-200 shrink-0 max-h-72 flex flex-col" data-testid="asset-detail">
            <div className="min-h-0 overflow-auto scrollbar px-3 pt-2.5 pb-2 space-y-2">
                <div className="flex items-baseline gap-2 min-w-0" title={id}>
                    <span className="font-semibold text-[13px] text-white truncate">{facts?.node.label ?? id}</span>
                    {facts?.node.kind && (
                        <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-gray-400">{facts.node.kind}</span>
                    )}
                </div>
                <dl className="grid grid-cols-[5.5rem_1fr] gap-x-2 gap-y-1 m-0">
                    {lines.map(([k, v]) => (
                        <React.Fragment key={k}>
                            <dt className="text-gray-400">{k}</dt>
                            <dd className="m-0 min-w-0 break-words">{v}</dd>
                        </React.Fragment>
                    ))}
                </dl>
                {facts && (
                    <Attributes
                        scope={scope}
                        provider={facts.node.provider}
                        collection={view.collection}
                        node={id}
                        subject={facts.badge?.at ?? null}
                    />
                )}
            </div>
            {ids.length > 1 && (
                <div
                    className="shrink-0 border-t border-gray-700/70 px-3 pt-1.5 text-gray-400"
                    title={ids.map((n) => rowFacts(view, n)?.node.label ?? n).join("\n")}
                >
                    {ids.length} selected — the actions below apply to all of them
                </div>
            )}
            <div
                className={`shrink-0 flex items-center gap-2 px-3 py-1.5 min-h-[2.5rem] ${ids.length > 1 ? "" : "border-t border-gray-700/70"}`}
                data-testid="asset-detail-actions"
            >
                <LoadControls view={view} ids={ids} scope={scope} requests={requests} />
                {requests.length > 0 && <RequestControls requests={requests} />}
                {actions &&<div className="ml-auto shrink-0 flex items-center">{actions}</div>}
            </div>
        </div>
    );
};

/** §Decision 6's "changed by" filter -- offered ONLY when `view.hasChangeOwners`
 *  (at least one loaded manifest carries a `publishedBy` or `sourceActor`);
 *  otherwise this renders nothing, not a disabled control, because a filter
 *  over zero owners is a dead end dressed up as an affordance. Narrows to a
 *  flat clickable list rather than pruning the tree itself -- the same choice
 *  `Orphans` below makes for the same reason: an owner is a property of a
 *  SUBJECT, not a shape the hierarchy needs to know about, and jumping
 *  `select()` to a match is enough to act on it. */
const ChangedByFilter: React.FC<{ view: AssetView; selected: string | null; onSelect: (id: string) => void }> = ({
    view,
    selected,
    onSelect,
}) => {
    const [owner, setOwner] = React.useState<string>("");
    if (!view.hasChangeOwners) return null;
    const owners = changeOwners(view);
    const matches = owner ? subjectsByOwner(view, owner) : [];
    return (
        <div className="px-1 pt-1 flex flex-wrap items-center gap-1 shrink-0">
            <select
                aria-label="Changed by"
                className={`${CONTROL} px-2 max-w-[70%] truncate`}
                value={owner}
                onChange={(e) => setOwner(e.target.value)}
            >
                <option value="">Changed by: anyone</option>
                {owners.map((o) => (
                    <option key={o.id} value={o.id}>
                        {o.display ? `${o.display} (${o.id})` : o.id}
                    </option>
                ))}
            </select>
            {owner && (
                <span className="text-[10px] text-gray-400 truncate">
                    {matches.length} subject(s)
                    {matches.map((id) => (
                        <button
                            key={id}
                            type="button"
                            className={`ml-1 rounded-sm px-1 ${selected === id ? "bg-blue-700 text-white" : "bg-gray-700 hover:text-white"}`}
                            onClick={() => onSelect(id)}
                        >
                            {id}
                        </button>
                    ))}
                </span>
            )}
        </div>
    );
};

const AssetsTab: React.FC = () => {
    const { useAssetBrowserStore, useScopeStore } = useViewerStores();
    const scope = scopeUrlPart(useScopeStore((s) => s.current));
    const loader = loaderFor(useAssetBrowserStore, assetsApi, sourceNodesApi);

    const storeScope = useAssetBrowserStore((s) => s.scope);
    const collections = useAssetBrowserStore((s) => s.collections);
    const collection = useAssetBrowserStore((s) => s.collection);
    const index = useAssetBrowserStore((s) => s.index);
    const indexLoading = useAssetBrowserStore((s) => s.indexLoading);
    const indexError = useAssetBrowserStore((s) => s.indexError);
    const mode = useAssetBrowserStore((s) => s.mode);
    const forest = useAssetBrowserStore((s) => s.forest);
    const forestVersion = useAssetBrowserStore((s) => s.forestVersion);
    const merged = useAssetBrowserStore((s) => s.mergedIndexRevisions);
    const expanded = useAssetBrowserStore((s) => s.expanded);
    const levelLoaded = useAssetBrowserStore((s) => s.levelLoaded);
    const levelLoading = useAssetBrowserStore((s) => s.levelLoading);
    const levelErrors = useAssetBrowserStore((s) => s.levelErrors);
    const sourceAnswer = useAssetBrowserStore((s) => s.sourceAnswer);
    const changedRows = useAssetBrowserStore((s) => s.changedRows);
    const evidenceAsked = useAssetBrowserStore((s) => s.evidenceAsked);
    const selected = useAssetBrowserStore((s) => s.selected);
    const searchTerm = useAssetBrowserStore((s) => s.searchTerm);
    const selection = useAssetBrowserStore((s) => s.selection);
    const { setMode, select, setSearchTerm } = useAssetBrowserStore.getState();

    // A scope switch invalidates everything; the first open of the tab loads.
    useEffect(() => {
        const s = useAssetBrowserStore.getState();
        if (s.scope !== scope) {
            s.resetForScope(scope);
            void loader.loadCollections(scope);
        } else if (s.collections === null && !s.indexLoading) {
            void loader.loadCollections(scope);
        }
    }, [scope, loader, useAssetBrowserStore]);

    // The mode decides which collection indexes form the tops of the tree.
    useEffect(() => {
        if (storeScope === scope && index) void loader.syncCollectionIndexes(scope);
    }, [mode, index, scope, storeScope, loader]);

    // eslint-disable-next-line react-hooks/exhaustive-deps
    const hierarchy = useMemo(() => buildAssetHierarchy(forest), [forestVersion]);
    const view = useMemo(
        () =>
            index && collection
                ? buildAssetView({
                      forest,
                      index,
                      collection,
                      mode,
                      indexRevisions: merged,
                      hierarchy,
                      levelLoaded,
                      sourceAnswer,
                      changedRows,
                      evidenceAsked,
                  })
                : null,
        // `forest` changes exactly when `hierarchy` does.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [hierarchy, index, collection, mode, merged, levelLoaded, sourceAnswer, changedRows, evidenceAsked],
    );

    const revisions = useMemo(() => (index && collection ? revisionsOf(index, collection) : []), [index, collection]);

    // --- how the tree is DRAWN (`@/assets/treeView`) ---------------------------
    const viewHints = useAssetBrowserStore((s) => s.viewHints);
    const viewDoc = useAssetBrowserStore((s) => s.viewDoc);
    const showHidden = useAssetBrowserStore((s) => s.showHidden);
    const treeStyle = useAssetBrowserStore((s) => s.treeStyle);
    // The Options panel (filter, marks, view, provider options) and which of its sections are open.
    const [optionsOpen, setOptionsOpen] = useState(false);
    const [openSections, setOpenSections] = useState<ReadonlySet<string>>(() => new Set(["filter", "view"]));
    const toggleSection = (id: string) =>
        setOpenSections((cur) => {
            const next = new Set(cur);
            if (!next.delete(id)) next.add(id);
            return next;
        });
    /** Open the Options panel at one section -- what a chip under the search box does. */
    const showOptions = (id: string) => {
        setOptionsOpen(true);
        setOpenSections((cur) => new Set([...cur, id]));
    };
    const [viewBusy, setViewBusy] = useState(false);
    const [viewError, setViewError] = useState<string | null>(null);

    // The scope's saved view for this collection, read once per choice of it.
    useEffect(() => {
        if (!collection || storeScope !== scope) return;
        let live = true;
        void readViewDoc(scope, collection).then((doc) => {
            if (live && useAssetBrowserStore.getState().collection === collection) {
                useAssetBrowserStore.getState().setViewDoc(doc);
            }
        });
        return () => {
            live = false;
        };
    }, [scope, collection, storeScope, useAssetBrowserStore]);

    const viewSettings = useMemo(() => resolveTreeView(viewDoc, viewHints), [viewDoc, viewHints]);

    // Lazy levels: an expanded row whose level below is not in (from the spine
    // that holds it, at that spine's revision) fetches ONE level -- its direct
    // children, never the whole spine. A row of a FLATTENED kind is never drawn
    // (its children take its place), so nobody can expand it: it opens as soon
    // as it is held, or the branch it sits in would draw empty. Nothing deeper
    // is prefetched. An errored level waits for an explicit retry rather than
    // looping.
    useEffect(() => {
        if (!view) return;
        const want = (id: string) => {
            const req = view.levelOf(id);
            const held = view.hierarchy.childrenOf(id).length > 0;
            if (!req || !levelWanted(view.hierarchy.byId.get(id)?.data, req, levelLoaded, held)) return;
            const key = levelKey(req);
            if (levelLoading.has(key) || levelErrors.has(key)) return;
            void loader.loadLevel(scope, req);
        };
        for (const id of expanded) want(id);
        if (viewSettings.flattenKinds.size) {
            for (const [id, node] of view.hierarchy.byId) {
                if (viewSettings.flattenKinds.has(normKind(node.data.kind))) want(id);
            }
        }
    }, [view, expanded, viewSettings, levelLoaded, levelLoading, levelErrors, loader, scope]);

    // Only WHETHER a search is on reaches the drawn hierarchy. Keyed on the term itself, every
    // keystroke rebuilt the whole tree for an answer that changes at most twice per search.
    const searchActive = isSearchTerm(searchTerm);
    // Provider filter: draw only the rows that ARE, or CONTAIN, something the chosen provider
    // published -- a claim of any weight (rooted here, covering from above, or rooted below). With
    // the server's geometry roll-up every row is judged, opened or not; without it, a row whose
    // levels below are not fetched yet cannot be judged and is kept: hiding it would hide a match
    // the tree simply has not read. Per collection, cleared when the collection changes.
    const [providerFilter, setProviderFilter] = useState<string>("");
    useEffect(() => setProviderFilter(""), [collection]);
    const geometryRollup = useAssetBrowserStore((s) => s.geometryRollup);
    const keepForProvider = useMemo(() => {
        if (!view || !providerFilter) return undefined;
        // GEOMETRY from the provider, not any claim: a tree published at the collection root covers
        // every row, so counting tree-only publishes made every provider match everything.
        const idx = geometryIndex(view, providerFilter, geometryRollup);
        return (id: string) => rowHasGeometry(view, idx, id);
    }, [view, providerFilter, geometryRollup]);
    // Tree sets (`@/assets/treeSets`): the scope's named sets of branches for this collection, and
    // the one this viewer narrowed the tree to. Read once per choice of collection, like the view.
    const treeSets = useTreeSetsStore((s) => s.sets);
    const activeSetId = useTreeSetsStore((s) => s.activeId);
    const setsBusy = useTreeSetsStore((s) => s.busy);
    const setsError = useTreeSetsStore((s) => s.error);
    const me = useMeStore((s) => s.displayName || s.email);
    const [setsOpen, setSetsOpen] = useState(false);
    useEffect(() => {
        if (collection && storeScope === scope) void useTreeSetsStore.getState().load(scope, collection);
    }, [scope, collection, storeScope]);
    const activeSet: TreeSet | null = treeSets.find((s) => s.id === activeSetId) ?? null;
    const setFilter = useMemo(() => (view && activeSet ? treeSetFilter(activeSet, view.hierarchy) : null), [view, activeSet]);
    const display = useMemo(
        () =>
            view
                ? displayHierarchy(view.hierarchy, viewSettings, {
                      searchActive,
                      showHidden,
                      keep: bothKeep(keepForProvider, setFilter?.keep),
                  })
                : null,
        [view, viewSettings, searchActive, showHidden, keepForProvider, setFilter],
    );
    const topKinds = useMemo(
        () => (view ? [...new Set(view.hierarchy.roots.map((id) => view.hierarchy.byId.get(id)?.data.kind ?? ""))] : []),
        [view],
    );

    // Written straight through, like every other control in the tab, and shown
    // at once; put back if the write fails.
    const saveView = async (doc: TreeViewDoc) => {
        if (!collection) return;
        const store = useAssetBrowserStore.getState();
        const previous = store.viewDoc;
        setViewBusy(true);
        setViewError(null);
        store.setViewDoc(doc);
        try {
            await writeViewDoc(scope, collection, doc);
        } catch (e) {
            useAssetBrowserStore.getState().setViewDoc(previous);
            setViewError(`Could not save the view: ${e instanceof Error ? e.message : String(e)}`);
        } finally {
            setViewBusy(false);
        }
    };
    const onViewChange = (next: TreeViewChange) =>
        void saveView(
            viewDocFor({ flattenKinds: next.flattenKinds, rootKinds: next.rootKinds, outOfScope: viewSettings.outOfScope }),
        );
    // Back to the provider's kinds; the out-of-scope list is the scope's own and stays.
    const onUseProviderDefaults = () =>
        void saveView({
            schema: VIEW_DOC_SCHEMA,
            out_of_scope: [...viewSettings.outOfScope].sort(),
            updated_at: new Date().toISOString(),
        });
    const setOutOfScope = (ids: readonly string[], out: boolean) => {
        const next = new Set(viewSettings.outOfScope);
        for (const id of ids) {
            if (out) next.add(id);
            else next.delete(id);
        }
        // Only the list changes: saved kind choices are kept as saved, and a
        // provider default stays a default rather than being frozen into the file.
        void saveView({
            ...(viewDoc ?? { schema: VIEW_DOC_SCHEMA }),
            out_of_scope: [...next].sort(),
            updated_at: new Date().toISOString(),
        });
    };
    // What the out-of-scope switch does for `ids`. Taking rows OUT wins over putting some back: a
    // selection that is partly out already is most likely being taken out the rest of the way. Null
    // where every row is out through a branch above it, which only that branch can change.
    const scopeSwitch = (ids: readonly string[]): { label: string; title: string; run: () => void } | null => {
        const many = ids.length > 1;
        const own = ids.filter((id) => viewSettings.outOfScope.has(id));
        const free = ids.filter((id) => !own.includes(id) && !(view && isOutOfScope(view.hierarchy, viewSettings.outOfScope, id)));
        if (free.length) {
            return {
                label: many ? `Out of scope (${free.length})` : "Out of scope",
                title: `Stop drawing ${many ? "these branches" : "this branch"} and everything under ${many ? "them" : "it"}, for everyone in this scope. Hides nothing that is published; Show hidden draws it again.`,
                run: () => setOutOfScope(free, true),
            };
        }
        if (own.length) {
            return {
                label: many ? `Back in scope (${own.length})` : "Back in scope",
                title: `Draw ${many ? "these branches" : "this branch"} again, for everyone in this scope`,
                run: () => setOutOfScope(own, false),
            };
        }
        return null;
    };
    // The out-of-scope switch for the selected rows, drawn on the detail's action row.
    const scopeToggle = (ids: readonly string[]): React.ReactNode => {
        const sw = scopeSwitch(ids);
        if (!sw) {
            return (
                <span className="text-gray-500" title="Out of scope through a branch above it.">
                    out of scope above
                </span>
            );
        }
        return (
            <button type="button" disabled={viewBusy} className={BTN_QUIET} title={sw.title} onClick={sw.run}>
                {sw.label}
            </button>
        );
    };
    const scopeMenuItem = (ids: readonly string[]): KebabMenuItem | null => {
        const sw = scopeSwitch(ids);
        return sw ? { key: "scope", label: sw.label, title: sw.title, disabled: viewBusy, separatorBefore: true, onClick: sw.run } : null;
    };
    // What an action on `id` applies to: the selection's topmost rows when `id` is in it, else `id`.
    const targetsOf = (id: string): string[] => actionTargets(selection, id, (n) => view?.hierarchy.byId.get(n)?.parent);
    // The selection's topmost rows, as set members: what the Sets panel adds or takes out.
    const selectedMembers = useMemo(
        () => (view && selected ? membersForIds(actionTargets(selection, selected, (n) => view.hierarchy.byId.get(n)?.parent), view.hierarchy) : []),
        [view, selection, selected],
    );
    // "Add to <set>" / "Remove from <set>" for `ids`, one per set, on a row's right-click menu.
    const setMenuItems = (ids: readonly string[]): KebabMenuItem[] => {
        if (!view) return [];
        return treeSets.map((s, i) => {
            const have = new Set(s.members.map((m) => m.id));
            const all = ids.every((id) => have.has(id));
            const sets = useTreeSetsStore.getState();
            return {
                key: `set:${s.id}`,
                label: all ? `Remove from set "${s.name}"` : `Add to set "${s.name}"`,
                disabled: setsBusy,
                separatorBefore: i === 0,
                onClick: () =>
                    void (all ? sets.removeMembers(s.id, [...ids]) : sets.addMembers(s.id, membersForIds(ids.filter((id) => !have.has(id)), view.hierarchy))),
            };
        });
    };
    const [rowMenu, setRowMenu] = useState<{ id: string; x: number; y: number } | null>(null);
    const [requestPickerAt, setRequestPickerAt] = useState<{ id: string; x: number; y: number } | null>(null);

    // Which providers can be asked for ONE node (`asset_node_request`), read off the live specs.
    const isAdmin = useMeStore((s) => s.isAdmin);
    const [nodeRequests, setNodeRequests] = useState<ReadonlyMap<string, AssetNodeRequest>>(new Map());
    // Which providers declare per-collection request options, and the collections they serve.
    const [optionProviders, setOptionProviders] = useState<
        ReadonlyArray<{ providerId: string; collections: readonly string[]; declared: AssetRequestOptions }>
    >([]);
    useEffect(() => {
        let live = true;
        viewerApi
            .listBackendPlugins()
            .then((res) => {
                if (!live) return;
                const byProvider = new Map<string, AssetNodeRequest>();
                const withOptions: { providerId: string; collections: readonly string[]; declared: AssetRequestOptions }[] = [];
                for (const p of assetProviderCollections(res.plugins ?? [])) {
                    if (p.nodeRequest) byProvider.set(p.providerId, p.nodeRequest);
                    if (p.requestOptions) withOptions.push({ providerId: p.providerId, collections: p.collections, declared: p.requestOptions });
                }
                setNodeRequests(byProvider);
                setOptionProviders(withOptions);
            })
            .catch(() => {
                // No specs, no request offered: the tree still browses.
            });
        return () => {
            live = false;
        };
    }, [scope]);
    // The providers that declare request options for THIS collection -- matched as the provider
    // spells its collections, which need not be the key's lower case.
    const collectionOptionProviders = useMemo(() => {
        const out = new Map<string, AssetRequestOptions>();
        const key = (collection ?? "").toLowerCase();
        for (const p of optionProviders) {
            if (p.collections.some((c) => c.toLowerCase() === key)) out.set(p.providerId, p.declared);
        }
        return out;
    }, [optionProviders, collection]);
    // Per node, so a request keeps its progress while the user looks at other rows. Both jobs are
    // in the toast as well, and a request that outlives the tab is under "Staged, not published".
    const [nodeRequestState, setNodeRequestState] = useState<
        ReadonlyMap<string, { busy: boolean; stage: string | null; error: string | null; note: string | null }>
    >(new Map());
    const patchNodeRequest = (key: string, patch: Partial<{ busy: boolean; stage: string | null; error: string | null; note: string | null }>) =>
        setNodeRequestState((cur) => {
            const next = new Map(cur);
            next.set(key, { ...(cur.get(key) ?? { busy: false, stage: null, error: null, note: null }), ...patch });
            return next;
        });
    // EVERY provider that can be asked for one node, not only the one whose spine drew the row:
    // the point of a second provider is geometry the first does not have. Each request publishes
    // under the provider that was asked, so its claim sits beside the others rather than on top.
    //
    // SEVERAL NODES (a selection) go in as few requests as the provider takes: batches of its
    // declared `maxNodes`, one job each, all started together. The state stays per (node,
    // provider), so each row still shows its own request; the control sums them.
    const nodeRequestsFor = (ids: readonly string[]): NodeRequestControl[] => {
        if (!view) return [];
        const targets: NodeTarget[] = [];
        for (const id of ids) {
            const node = view.hierarchy.byId.get(id)?.data;
            if (node) targets.push({ id, label: node.label });
        }
        if (!targets.length) return [];
        const collection = view.collection;
        const many = targets.length > 1;
        return [...nodeRequests.entries()]
            .sort(([a], [b]) => a.localeCompare(b))
            .map(([providerId, req]) => {
                const states = targets.map((t) => nodeRequestState.get(loadKey(t.id, providerId)));
                const running = states.filter((st) => st?.busy);
                const errors = targets.flatMap((t, i) => (states[i]?.error && !states[i]?.busy ? [`${t.label ?? t.id}: ${states[i]!.error}`] : []));
                const notes = states.flatMap((st) => (st?.note && !st.busy ? [st.note] : []));
                return {
                    provider: providerId,
                    label: many ? `${req.label} (${targets.length})` : req.label,
                    busy: running.length > 0,
                    stage: many ? (running.length ? `${running.length} of ${targets.length} running` : null) : (states[0]?.stage ?? null),
                    error: errors.length ? (many ? `${errors.length} failed:\n${errors.join("\n")}` : states[0]!.error) : null,
                    note: notes.length ? (many ? `${notes.length} of ${targets.length} up to date` : notes[0]) : null,
                    blocked: req.requiresAdmin && !isAdmin ? `Only an administrator can run ${req.pluginId}` : null,
                    onDemand: !!req.onDemand,
                    run: async () => {
                        const results = await Promise.all(
                            nodeBatches(req, targets).map((batch) => {
                                const keys = batch.map((t) => loadKey(t.id, providerId));
                                const patch = (p: Parameters<typeof patchNodeRequest>[1]) => keys.forEach((k) => patchNodeRequest(k, p));
                                patch({ busy: true, stage: null, error: null, note: null });
                                return requestNodes(requestDeps((stage) => patch({ stage })), scope, providerId, req, collection, batch)
                                    .then((out) => {
                                        // Unchanged: the provider's last publish already covers these nodes, so
                                        // nothing new was published -- the note says which one.
                                        patch({ busy: false, stage: null, note: out.unchanged ? `up to date (${out.revision})` : null });
                                        return true;
                                    })
                                    .catch((e) => {
                                        patch({ busy: false, stage: null, error: e instanceof Error ? e.message : String(e) });
                                        return false;
                                    });
                            }),
                        );
                        // Re-read, so the new publish's claims reach the rows and Load enables -- once,
                        // after every batch, rather than once per batch.
                        await loader.refresh(scope);
                        return results.every(Boolean);
                    },
                };
            });
    };

    // "Load set" asking one provider for the members it has no geometry for: the same request, in
    // the same batches and with the same progress per row, as the row's own "Request" action.
    const requestMissing: RequestMissing = (provider, ids) => {
        const control = nodeRequestsFor(ids).find((r) => r.provider === provider);
        return control ? { blocked: control.blocked, run: control.run } : null;
    };

    // A request publishes into this scope: re-read, then show what arrived.
    const onPublished = async (published: string) => {
        await loader.refresh(scope);
        if (useAssetBrowserStore.getState().collections?.includes(published)) {
            await loader.chooseCollection(scope, published);
        }
    };
    // ONE MOUNT POINT FOR THE REQUEST PANEL, whatever the tab below it shows. A
    // request that publishes into an empty scope turns "nothing published" into a
    // tree, and a panel mounted in each branch separately would be torn down by
    // that very change -- taking the outcome it was about to report with it.
    return (
        <div className="flex flex-col h-full min-h-0 text-white">
            <div className="shrink-0">
                <RequestCollection scope={scope} onPublished={(c) => void onPublished(c)} onChanged={() => void loader.refresh(scope)} />
            </div>
            {renderBody()}
        </div>
    );

    // Plain render functions, CALLED rather than mounted: a component declared in
    // here would be a new type every render, and React would remount the tree
    // under it -- expansion, selection and scroll with it -- on every keystroke.
    function renderBody(): React.ReactElement {
        if (indexError && !index) {
            return <Banner tone="error">Could not read the asset index: {indexError}</Banner>;
        }
        if (!collections || (indexLoading && !index)) {
            return <div className="p-2 text-xs text-gray-400">Reading published assets…</div>;
        }
        if (!collections.length) {
            return (
                <div className="p-2 text-xs text-gray-400">
                    Nothing is published under <code>assets/</code> in this scope.
                </div>
            );
        }
        return renderPublished();
    }

    function renderPublished(): React.ReactElement {
        const summary = view?.summary;
        return (
            <div className="flex flex-col flex-1 min-h-0">
                <div className="px-2 pt-2 flex items-center gap-1.5 shrink-0">
                    <select
                        aria-label="Collection"
                        className={`${CONTROL} px-2 min-w-0 flex-1 truncate font-medium`}
                        value={collection ?? ""}
                        onChange={(e) => void loader.chooseCollection(scope, e.target.value)}
                    >
                        {(collections ?? []).map((c) => (
                            // Shown as the provider writes it (upper case); the collection key is
                            // lower-case by construction and stays the value.
                            <option key={c} value={c}>
                                {c.toUpperCase()}
                            </option>
                        ))}
                    </select>
                    <ModePicker mode={mode} revisions={revisions} onChange={setMode} />
                    <IconButton label="Refresh — re-read the index and rebuild the tree from nothing" onClick={() => void loader.refresh(scope)}>
                        <path d="M13 8a5 5 0 1 1-1.5-3.6M13 2.5V5h-2.5" />
                    </IconButton>
                    <IconButton
                        label="Sets — named sets of branches the tree can be narrowed to, shared in this scope"
                        pressed={setsOpen || !!activeSet}
                        onClick={() => setSetsOpen((o) => !o)}
                    >
                        <path d="M2.5 3.5h11M2.5 8h11M2.5 12.5h6M11 11v3M9.5 12.5h3" />
                    </IconButton>
                    <IconButton
                        label="Options — provider filter, row marks and legend, how the tree is drawn, and provider options"
                        pressed={optionsOpen}
                        onClick={() => setOptionsOpen((o) => !o)}
                    >
                        <path d="M2 4h7M12 4h2M2 12h3M8 12h6M9 2.5v3M5 10.5v3" />
                    </IconButton>
                </div>
                {optionsOpen && (
                    <TreeOptionsPanel
                        open={openSections}
                        onToggle={toggleSection}
                        sections={[
                            ...((view?.contentProviders.length ?? 0) > 0
                                ? [
                                      {
                                          id: "filter",
                                          title: "Provider filter",
                                          badge: providerFilter || null,
                                          content: (
                                              <select
                                                  aria-label="Provider filter"
                                                  className={`${CONTROL} w-full px-2 ${providerFilter ? "border-blue-400 text-blue-200" : ""}`}
                                                  value={providerFilter}
                                                  onChange={(e) => setProviderFilter(e.target.value)}
                                                  title="Show only rows that are, or contain, something published by this provider"
                                              >
                                                  <option value="">All providers</option>
                                                  {view!.contentProviders.map((p) => (
                                                      <option key={p} value={p}>
                                                          {p}
                                                      </option>
                                                  ))}
                                              </select>
                                          ),
                                      },
                                  ]
                                : []),
                            {
                                id: "marks",
                                title: "Row marks and legend",
                                content: <TreeLegend providers={view?.contentProviders ?? []} geometryProvider={providerFilter || null} />,
                            },
                            {
                                id: "view",
                                title: "View",
                                badge: viewSettings.rootKinds ? `top: ${[...viewSettings.rootKinds].join(", ")}` : null,
                                content: (
                                    <TreeViewPanel
                                        settings={viewSettings}
                                        hints={viewHints}
                                        topKinds={topKinds}
                                        rootKindCensus={display?.rootKindCensus ?? new Map()}
                                        busy={viewBusy}
                                        error={viewError}
                                        onChange={onViewChange}
                                        onUseProviderDefaults={onUseProviderDefaults}
                                        treeStyle={treeStyle}
                                        onTreeStyle={(s) => useAssetBrowserStore.getState().setTreeStyle(s)}
                                    />
                                ),
                            },
                            ...(collection && collectionOptionProviders.size > 0
                                ? [
                                      {
                                          id: "provider-options",
                                          title: "Provider options",
                                          content: (
                                              <ProviderOptionsPanel
                                                  scope={scope}
                                                  collection={collection}
                                                  providers={collectionOptionProviders}
                                                  deps={requestDeps}
                                              />
                                          ),
                                      },
                                  ]
                                : []),
                        ]}
                    />
                )}
                {!(optionsOpen && openSections.has("view")) && viewError && <Banner tone="error">{viewError}</Banner>}
                {setsOpen && (
                    <TreeSetsPanel
                        sets={treeSets}
                        activeId={activeSet?.id ?? null}
                        missing={setFilter?.missing ?? []}
                        selected={selectedMembers}
                        busy={setsBusy}
                        error={setsError}
                        onActivate={(id) => useTreeSetsStore.getState().setActive(id)}
                        onCreate={(name, members) =>
                            void useTreeSetsStore
                                .getState()
                                .create(name, members, me)
                                .then((s) => s && useTreeSetsStore.getState().setActive(s.id))
                        }
                        onRename={(id, name) => void useTreeSetsStore.getState().rename(id, name)}
                        onDelete={(id) => void useTreeSetsStore.getState().remove(id)}
                        onAdd={(id, members) => void useTreeSetsStore.getState().addMembers(id, members)}
                        onRemove={(id, ids) => void useTreeSetsStore.getState().removeMembers(id, ids)}
                        providers={view?.contentProviders ?? []}
                        onSetProviders={(id, choices) => void useTreeSetsStore.getState().setProviders(id, choices)}
                        loadControl={view && activeSet ? <SetLoad view={view} set={activeSet} scope={scope} requestMissing={requestMissing} /> : null}
                        onReveal={(id) => {
                            const open: string[] = [];
                            let p = view?.hierarchy.byId.get(id)?.parent ?? null;
                            while (p) {
                                open.push(p);
                                p = view?.hierarchy.byId.get(p)?.parent ?? null;
                            }
                            useAssetBrowserStore.getState().revealRow(id, open);
                        }}
                    />
                )}
                {!setsOpen && setsError && <Banner tone="error">{setsError}</Banner>}
                {!setsOpen && view && activeSet && (
                    <div className="px-2 pt-2 flex items-center gap-2 text-xs shrink-0">
                        <button
                            type="button"
                            className="rounded-full bg-blue-900/60 px-2 py-0.5 text-blue-100 hover:bg-blue-800 truncate"
                            title="The tree is narrowed to this set. Manage it under Sets."
                            onClick={() => setSetsOpen(true)}
                        >
                            Set: {activeSet.name}
                            {(setFilter?.missing.length ?? 0) > 0 && ` · ${setFilter!.missing.length} not in tree`}
                        </button>
                        <SetLoad view={view} set={activeSet} scope={scope} requestMissing={requestMissing} />
                    </div>
                )}
                <div className="px-2 pt-2 shrink-0">
                    <div className={`${CONTROL} flex items-center gap-2 px-2`}>
                        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" className="text-gray-400 shrink-0">
                            <circle cx="7" cy="7" r="4.5" />
                            <path d="M10.5 10.5 14 14" />
                        </svg>
                        <input
                            aria-label="Search assets"
                            className="flex-1 min-w-0 bg-transparent outline-none text-[13px] text-gray-100 placeholder:text-gray-500"
                            placeholder="Search names and refs"
                            title={`Case-insensitive; searches from ${MIN_SEARCH_CHARS} characters. Searches loaded rows only -- branches are fetched a level at a time as they are opened`}
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                        />
                    </div>
                </div>
                {display && (display.hiddenRoots > 0 || viewSettings.outOfScope.size > 0 || display.rootFilterStoodDown || providerFilter) && (
                    <div className="px-2 pt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-400 shrink-0">
                        {providerFilter && (
                            <button
                                type="button"
                                className="rounded-full bg-blue-900/60 px-2 py-0.5 text-blue-100 hover:bg-blue-800"
                                title="Only rows that are, or contain, geometry from this provider are drawn. Click to show every provider."
                                onClick={() => setProviderFilter("")}
                            >
                                Provider: {providerFilter} ×
                            </button>
                        )}
                        {viewSettings.rootKinds && !display.rootFilterStoodDown && !searchActive && (
                            <button
                                type="button"
                                className="rounded-full bg-gray-700/70 px-2 py-0.5 text-gray-200 hover:bg-gray-600"
                                title="Only these kinds are drawn at the top level. Change it under Options ▸ View."
                                onClick={() => showOptions("view")}
                            >
                                Top: {[...viewSettings.rootKinds].join(", ")}
                            </button>
                        )}
                        {display.hiddenRoots > 0 && (
                            <span title="Top-level branches of other kinds are not drawn">
                                {display.hiddenRoots} other branch{display.hiddenRoots === 1 ? "" : "es"} hidden
                            </span>
                        )}
                        {display.rootFilterStoodDown && (
                            <span title="None of the chosen kinds is at the top, so every branch is drawn">
                                top-level filter matches nothing — showing all
                            </span>
                        )}
                        {viewSettings.outOfScope.size > 0 && (
                            <label className="ml-auto flex items-center gap-1 cursor-pointer hover:text-gray-200">
                                <input
                                    type="checkbox"
                                    className="accent-blue-400"
                                    checked={showHidden}
                                    onChange={(e) => useAssetBrowserStore.getState().setShowHidden(e.target.checked)}
                                />
                                show {viewSettings.outOfScope.size} out of scope
                            </label>
                        )}
                    </div>
                )}
                <div className="mt-2 border-t border-gray-700/70 shrink-0" />
                {view && <ChangedByFilter view={view} selected={selected} onSelect={select} />}

                <div className="shrink-0">
                    {indexError && <Banner tone="error">{indexError}</Banner>}
                    {summary?.mixed && (
                        <Banner tone="warn" title={summary.revisions.map(formatRevision).join("\n")}>
                            Mixed: this view spans {summary.revisions.length} publishes (
                            {formatRevision(summary.revisions[0])} … {formatRevision(summary.revisions[summary.revisions.length - 1])}). Pick a
                            run for a coeval view.
                        </Banner>
                    )}
                    {summary?.coeval && mode.kind === "run" && (
                        <Banner tone="info">
                            Run {formatRevision(mode.revision)} — coeval
                            {summary.missingCount > 0 && `; ${summary.missingCount} subject(s) have nothing in this run`}
                        </Banner>
                    )}
                    {view && view.staleCount > 0 && (
                        <Banner tone="warn">
                            {view.staleCount} row(s) are drawn from a hierarchy the resolution has moved past. Refresh to rebuild.
                        </Banner>
                    )}
                    {/* BEHIND is not STALE: stale says our own tree lags the resolution
                        (fixed by Refresh); behind says the SOURCE moved after a root was
                        published (fixed only by a new export). Different tone ("error", not
                        "warn"), different verb, so the two are never mistaken for one banner
                        said twice -- see `@/assets/changes`'s module comment. */}
                    {view && view.changes.behind > 0 && (
                        <Banner tone="error">
                            {view.changes.behind} published root(s) are behind their source — it changed after this was
                            published. Re-export to catch up; Refresh will not fix this.
                        </Banner>
                    )}
                    {view && view.drift.size > 0 && (
                        <Banner tone="warn">{view.drift.size} subject(s) were published against an older tree than the one shown.</Banner>
                    )}
                    {view && view.providers.length > 1 && (
                        <Banner tone="info">Mixed collection — providers: {view.providers.join(", ")}</Banner>
                    )}
                    {summary && summary.incompleteCount > 0 && (
                        <Banner tone="warn">
                            {summary.incompleteCount} subject(s) have a half-written newest revision (no manifest); the last complete one is shown.
                        </Banner>
                    )}
                    {view && view.manifestErrors.size > 0 && (
                        <Banner tone="error">{view.manifestErrors.size} manifest(s) could not be read.</Banner>
                    )}
                    {view && view.malformedKeys.length > 0 && (
                        <Banner tone="error" title={view.malformedKeys.join("\n")}>
                            {view.malformedKeys.length} key(s) under assets/ do not parse.
                        </Banner>
                    )}
                    {view && view.spineRevision === null && (
                        <Banner tone="info">No collection hierarchy is published for this resolution; rows come from subject spines only.</Banner>
                    )}
                </div>

                <div className="flex-1 min-h-0 flex flex-col">
                    {view && display && (
                        <AssetTree
                            view={view}
                            display={display.hierarchy}
                            geometryProvider={providerFilter || null}
                            outOfScope={viewSettings.outOfScope}
                            showHidden={showHidden}
                            onRetryLevel={(req) => void loader.loadLevel(scope, req)}
                            onRowContextMenu={(id, x, y) => setRowMenu({ id, x, y })}
                        />
                    )}
                </div>
                {view && (
                    <Orphans
                        orphans={view.orphans}
                        pending={view.pending}
                        unmergedSpines={view.unmergedSpines.length}
                        loadingSpines={levelLoading.size > 0}
                        selected={selected}
                        onSelect={select}
                        onPlace={() => void loader.loadLevels(scope, view.unmergedSpines)}
                    />
                )}
                {view && selected && (
                    <Detail
                        view={view}
                        id={selected}
                        ids={targetsOf(selected)}
                        scope={scope}
                        requests={nodeRequestsFor(targetsOf(selected))}
                        actions={view.hierarchy.byId.has(selected) ? scopeToggle(targetsOf(selected)) : null}
                    />
                )}
                {view && rowMenu && view.hierarchy.byId.has(rowMenu.id) && (
                    <AssetRowMenu
                        view={view}
                        id={rowMenu.id}
                        ids={targetsOf(rowMenu.id)}
                        scope={scope}
                        x={rowMenu.x}
                        y={rowMenu.y}
                        requests={nodeRequestsFor(targetsOf(rowMenu.id))}
                        onPickRequests={(x, y) => setRequestPickerAt({ id: rowMenu.id, x, y })}
                        scopeItem={scopeMenuItem(targetsOf(rowMenu.id))}
                        setItems={setMenuItems(targetsOf(rowMenu.id))}
                        onClose={() => setRowMenu(null)}
                    />
                )}
                {view && requestPickerAt && view.hierarchy.byId.has(requestPickerAt.id) && (
                    <RequestPicker
                        requests={nodeRequestsFor(targetsOf(requestPickerAt.id))}
                        anchor={{ x: requestPickerAt.x, y: requestPickerAt.y }}
                        onClose={() => setRequestPickerAt(null)}
                    />
                )}
            </div>
        );
    }
};

export default AssetsTab;
