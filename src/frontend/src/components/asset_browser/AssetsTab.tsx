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
import { orphanHeading, orphanSentence, type OrphanEntry } from "@/assets/orphans";
import { rowFacts } from "@/assets/rowFacts";
import { canFetchSpine } from "@/assets/spines";
import type { ResolutionMode } from "@/assets/types";
import { assetsApi } from "@/services/api/assets";
import { useViewerStores } from "@/state/AdaViewerContext";
import { loaderFor } from "@/state/assetBrowserLoader";
import { scopeUrlPart } from "@/state/scopeStore";

import AssetTree from "./AssetTree";
import { formatRevision } from "./format";

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

const Detail: React.FC<{ view: AssetView; id: string }> = ({ view, id }) => {
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
        </div>
    );
};

const AssetsTab: React.FC = () => {
    const { useAssetBrowserStore, useScopeStore } = useViewerStores();
    const scope = scopeUrlPart(useScopeStore((s) => s.current));
    const loader = loaderFor(useAssetBrowserStore, assetsApi);

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
                ? buildAssetView({ forest, index, collection, mode, indexRevisions: merged, hierarchy, spineLoaded })
                : null,
        // `forest` changes exactly when `hierarchy` does.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [hierarchy, index, collection, mode, merged, spineLoaded],
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
            {view && selected && <Detail view={view} id={selected} />}
        </div>
    );
};

export default AssetsTab;
