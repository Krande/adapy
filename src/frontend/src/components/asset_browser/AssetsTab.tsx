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

import React, { useEffect, useMemo } from "react";

import { revisionsOf } from "@/assets/assetIndex";
import { buildAssetHierarchy, buildAssetView, type AssetView } from "@/assets/assetView";
import type { ChangeState } from "@/assets/changes";
import {
    assetSourceName,
    loadNode,
    parseDeliveryClaim,
    type LoadNodeDeps,
    type NodeRef,
} from "@/assets/delivery";
import { orphanHeading, orphanSentence, type OrphanEntry } from "@/assets/orphans";
import { changeOwners, rowFacts, subjectsByOwner, type RowBadge } from "@/assets/rowFacts";
import { canFetchSpine } from "@/assets/spines";
import type { ResolutionMode } from "@/assets/types";
import type { TreeNodeData } from "@/components/tree_view/CustomNode";
import { makePluginContextStandalone } from "@/plugins";
import { assetsApi } from "@/services/api/assets";
import { conversionApi } from "@/services/api/conversion";
import { filesApi } from "@/services/api/files";
import { sourceNodesApi } from "@/services/api/sourceNodes";
import { useViewerStores } from "@/state/AdaViewerContext";
import { loaderFor } from "@/state/assetBrowserLoader";
import { useModelSessionStore } from "@/state/modelSession";
import { requestRender } from "@/state/perfStore";
import { scopeUrlPart } from "@/state/scopeStore";
import { getViewerRuntime } from "@/state/viewerRuntime";
import { selectTreeNode } from "@/utils/tree_view/treeNavigation";

import AssetTree from "./AssetTree";
import { formatRevision } from "./format";

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
function realDeliveryDeps(isLoaded: (sourceName: string) => boolean): LoadNodeDeps {
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
        provider: owner.provider,
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

const ModePicker: React.FC<{ mode: ResolutionMode; revisions: readonly string[]; onChange: (m: ResolutionMode) => void }> = ({
    mode,
    revisions,
    onChange,
}) => {
    const newest = revisions[revisions.length - 1];
    return (
        <div className="flex items-center gap-1 min-w-0">
            <select
                aria-label="Resolution"
                className="bg-gray-600 text-white rounded-sm text-xs px-1 py-0.5"
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
                    className="bg-gray-600 text-white rounded-sm text-xs px-1 py-0.5 min-w-0 truncate"
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
                <button
                    type="button"
                    className="ml-auto shrink-0 pl-2 text-blue-300 hover:text-white disabled:text-gray-500"
                    disabled={loadingSpines}
                    onClick={onPlace}
                    title={`Fetch the ${unmergedSpines} unopened published hierarch${unmergedSpines === 1 ? "y" : "ies"} to place them`}
                >
                    {loadingSpines ? "placing…" : "place"}
                </button>
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

/** Load / reveal / unload for one row, driven off the same `RowBadge` the
 *  tree's badge dot reads (`rowFacts`) -- a `solid` or `ghost` badge is
 *  deliverable; `below` is not (the content is further DOWN the tree, so
 *  there is nothing at or above this row to load) and gets no control. */
const LoadControls: React.FC<{ view: AssetView; id: string; scope: string }> = ({ view, id, scope }) => {
    const { useAssetBrowserStore, useModelState, useTreeViewStore } = useViewerStores();
    const busy = useAssetBrowserStore((s) => s.loadBusy.has(id));
    const error = useAssetBrowserStore((s) => s.loadErrors.get(id) ?? null);
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

    const facts = rowFacts(view, id);
    const badge = facts?.badge;
    if (!badge || badge.weight === "below") return null;
    const ref = refForBadge(view, id, badge);
    if (!ref) return null;
    const sourceName = assetSourceName(ref);
    const entry = loaded.find((a) => a.sourceName === sourceName);

    if (entry) {
        const root = loadedTreeRoot(treeData, sourceName);
        return (
            <div className="flex items-center gap-2 pt-1 flex-wrap">
                <span className="text-green-300">Loaded{badge.weight === "ghost" ? ` (via ${badge.at})` : ""}</span>
                <button
                    type="button"
                    className="text-blue-300 hover:text-white disabled:text-gray-500"
                    disabled={!root}
                    title={root ? "Select this model's root in the Files tab" : "Not in the Files tree yet"}
                    onClick={() => root && void selectTreeNode(root)}
                >
                    reveal in Files
                </button>
                <button
                    type="button"
                    className="text-gray-300 hover:text-white"
                    onClick={() => {
                        makePluginContextStandalone(OWNER).scene.unloadModel(sourceName);
                        requestRender();
                        // Optimistic: the effect above will re-confirm against
                        // `loadedSourceNames` on the next render regardless.
                        useAssetBrowserStore.getState().reconcileLoaded(new Set([...liveSourceNames].filter((n) => n !== sourceName)));
                    }}
                >
                    unload
                </button>
            </div>
        );
    }

    return (
        <div className="flex items-center gap-2 pt-1 flex-wrap">
            <button
                type="button"
                disabled={busy}
                className="text-blue-300 hover:text-white disabled:text-gray-500"
                onClick={() => {
                    const store = useAssetBrowserStore.getState();
                    store.beginLoad(id);
                    void (async () => {
                        try {
                            const wireClaim = await assetsApi.getAssetDelivery(scope, ref.provider, ref.collection, ref.subject, {
                                revision: ref.revision,
                            });
                            const claim = parseDeliveryClaim(wireClaim);
                            const deps = realDeliveryDeps((name) => useModelState.getState().loadedSourceNames.has(name));
                            const asset = await loadNode(deps, scope, ref, claim);
                            useAssetBrowserStore.getState().endLoad(id, asset);
                            requestRender();
                        } catch (e) {
                            useAssetBrowserStore.getState().failLoad(id, e instanceof Error ? e.message : String(e));
                        }
                    })();
                }}
            >
                {busy ? "loading…" : badge.weight === "ghost" ? `Load (from ${badge.at})` : "Load"}
            </button>
            {error && (
                <span className="text-red-300 truncate" title={error}>
                    {error}
                </span>
            )}
        </div>
    );
};

const Detail: React.FC<{ view: AssetView; id: string; scope: string }> = ({ view, id, scope }) => {
    const facts = rowFacts(view, id);
    const orphan = view.orphans.find((o) => o.id === id);
    const resolved = view.resolution.subjects.get(id);
    const manifest = resolved?.revision.manifest ?? null;
    const lines: [string, React.ReactNode][] = [];
    if (facts) {
        lines.push(["Kind", facts.node.kind || "—"]);
        lines.push(["Provider", facts.node.provider]);
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
        <div className="border-t border-gray-700 px-2 py-1 text-xs text-gray-200 shrink-0 max-h-40 overflow-auto" data-testid="asset-detail">
            <div className="font-semibold text-white truncate" title={id}>
                {facts?.node.label ?? id} <span className="text-gray-400 font-normal">{id}</span>
            </div>
            {lines.map(([k, v]) => (
                <div key={k} className="flex gap-2">
                    <span className="text-gray-400 w-20 shrink-0">{k}</span>
                    <span className="min-w-0 break-words">{v}</span>
                </div>
            ))}
            <LoadControls view={view} id={id} scope={scope} />
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
                className="bg-gray-600 text-white rounded-sm text-xs px-1 py-0.5 max-w-[55%] truncate"
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
    const spineLoaded = useAssetBrowserStore((s) => s.spineLoaded);
    const spineLoading = useAssetBrowserStore((s) => s.spineLoading);
    const spineErrors = useAssetBrowserStore((s) => s.spineErrors);
    const sourceAnswer = useAssetBrowserStore((s) => s.sourceAnswer);
    const changedRows = useAssetBrowserStore((s) => s.changedRows);
    const evidenceAsked = useAssetBrowserStore((s) => s.evidenceAsked);
    const selected = useAssetBrowserStore((s) => s.selected);
    const searchTerm = useAssetBrowserStore((s) => s.searchTerm);
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
                      spineLoaded,
                      sourceAnswer,
                      changedRows,
                      evidenceAsked,
                  })
                : null,
        // `forest` changes exactly when `hierarchy` does.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [hierarchy, index, collection, mode, merged, spineLoaded, sourceAnswer, changedRows, evidenceAsked],
    );

    // Lazy spines: an expanded row whose covering spine is not in (at the
    // revision the resolution names) fetches it. An errored spine waits for an
    // explicit retry rather than looping.
    useEffect(() => {
        if (!view) return;
        for (const id of expanded) {
            const node = view.hierarchy.byId.get(id)?.data;
            const source = view.spines.get(id) ?? null;
            if (!source || !canFetchSpine(node, source, spineLoaded)) continue;
            if (spineLoading.has(source.root) || spineErrors.has(source.root)) continue;
            void loader.loadSpine(scope, source);
        }
    }, [view, expanded, spineLoaded, spineLoading, spineErrors, loader, scope]);

    const revisions = useMemo(() => (index && collection ? revisionsOf(index, collection) : []), [index, collection]);

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

    const summary = view?.summary;
    return (
        <div className="flex flex-col h-full min-h-0 text-white">
            <div className="px-1 pt-1 flex flex-wrap items-center gap-1 shrink-0">
                <select
                    aria-label="Collection"
                    className="bg-gray-600 text-white rounded-sm text-xs px-1 py-0.5 max-w-[45%] truncate"
                    value={collection ?? ""}
                    onChange={(e) => void loader.chooseCollection(scope, e.target.value)}
                >
                    {collections.map((c) => (
                        <option key={c} value={c}>
                            {c}
                        </option>
                    ))}
                </select>
                <ModePicker mode={mode} revisions={revisions} onChange={setMode} />
                <button
                    type="button"
                    className="ml-auto text-xs text-gray-300 hover:text-white px-1"
                    onClick={() => void loader.refresh(scope)}
                    title="Re-read the index and rebuild the tree from nothing"
                >
                    ⟳
                </button>
            </div>
            <div className="px-1 pt-1 shrink-0">
                <input
                    className="w-full bg-gray-600 text-white rounded-sm pl-1 text-sm"
                    placeholder="Search assets"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                />
            </div>
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
                {view && (
                    <AssetTree
                        view={view}
                        onRetrySpine={(source) => void loader.loadSpine(scope, source)}
                    />
                )}
            </div>
            {view && (
                <Orphans
                    orphans={view.orphans}
                    pending={view.pending}
                    unmergedSpines={view.unmergedSpines.length}
                    loadingSpines={spineLoading.size > 0}
                    selected={selected}
                    onSelect={select}
                    onPlace={() => void loader.loadSpines(scope, view.unmergedSpines)}
                />
            )}
            {view && selected && <Detail view={view} id={selected} scope={scope} />}
        </div>
    );
};

export default AssetsTab;
