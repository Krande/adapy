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
import { revisionsOf } from "@/assets/assetIndex";
import { buildAssetHierarchy, buildAssetView } from "@/assets/assetView";
import { VIEW_DOC_SCHEMA, displayHierarchy, isOutOfScope, normKind, resolveTreeView, viewDocFor } from "@/assets/treeView";
import type { ChangeAction } from "@/assets/changes";
import { sourcesTabAccess } from "@/assets/sourcesTabAccess";
import { geometryIndex, rowHasGeometry } from "@/assets/geometryMarks";
import { providerIdTitle } from "@/assets/providerNames";
import { isSearchTerm } from "@/assets/rowFacts";
import { levelKey, levelWanted } from "@/assets/spines";
import { actionTargets } from "@/assets/treeKeys";
import { bothKeep, loadsProvider, membersForIds, setMembership, treeSetFilter, type TreeSet } from "@/assets/treeSets";
import type { KebabMenuItem } from "@/components/common/PositionedMenu";
import { assetsApi } from "@/services/api/assets";
import { sourceNodesApi } from "@/services/api/sourceNodes";
import { useMeStore } from "@/state/meStore";
import { useProviderName } from "@/state/providerNamesStore";
import { useTreeSetsStore } from "@/state/treeSetsStore";
import { useViewerStores } from "@/state/AdaViewerContext";
import { loaderFor } from "@/state/assetBrowserLoader";
import { scopeUrlPart } from "@/state/scopeStore";
import AssetTree, { type SetEditing } from "./AssetTree";
import { ancestorsOf } from "@/assets/hierarchy";
import { formatRevision } from "./format";
import ChangeRunsPanel from "./ChangeRunsPanel";
import AttributeSearchSection from "./AttributeSearchSection";
import RequestCollection, { requestDeps } from "./RequestCollection";
import TreeLegend from "./TreeLegend";
import ProviderOptionsPanel from "./ProviderOptionsPanel";
import TreeOptionsPanel from "./TreeOptionsPanel";
import TreeServerHits from "./TreeServerHits";
import SourcesSearchBox from "./SourcesSearchBox";
import TreeSetsPanel from "./TreeSetsPanel";
import TreeViewPanel, { type TreeViewChange } from "./TreeViewPanel";
import { BTN_QUIET, Banner, CONTROL, IconButton, ModePicker, Orphans, PendingChip } from "./assetsTab/chrome";
import type { RequestMissing } from "./assetsTab/loads";
import { SetLoad } from "./assetsTab/LoadControls";
import { ChangedByFilter } from "./assetsTab/ChangedByFilter";
import { Detail } from "./assetsTab/Detail";
import { AssetRowMenu } from "./assetsTab/AssetRowMenu";
import { RequestPicker } from "./assetsTab/RequestControls";
import { useNodeRequests, useProviderRequestSpecs } from "./assetsTab/useNodeRequests";
import { useSavedTreeView } from "./assetsTab/useSavedTreeView";

export { realDeliveryDeps } from "./assetsTab/delivery";
export { requestRowLoad } from "./assetsTab/loads";

const AssetsTab: React.FC = () => {
    const { useAssetBrowserStore, useScopeStore } = useViewerStores();
    const scope = scopeUrlPart(useScopeStore((s) => s.current));
    const loader = loaderFor(useAssetBrowserStore, assetsApi, sourceNodesApi);
    // Display names for provider ids, in this scope; ids stay what everything keys on.
    const pn = useProviderName();

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

    // A picked change-check run's marks (`ChangeRunsPanel`), drawn INSTEAD of the change feed's
    // evidence marks while it is picked -- see `@/assets/changeRuns` for why one replaces the other.
    const [runMarkOverride, setRunMarkOverride] = useState<ReadonlyMap<string, ChangeAction> | null>(null);

    // eslint-disable-next-line react-hooks/exhaustive-deps
    const hierarchy = useMemo(() => buildAssetHierarchy(forest), [forestVersion]);
    const baseView = useMemo(
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
    const view = useMemo(
        () => (baseView && runMarkOverride ? { ...baseView, evidenceMarks: runMarkOverride } : baseView),
        [baseView, runMarkOverride],
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
    // The scope's saved view for this collection: read once per choice of it, written straight through.
    const { viewBusy, viewError, saveView } = useSavedTreeView(useAssetBrowserStore, scope, storeScope, collection);

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
    // Editing the active set's members in the tree ("Edit" in the Sets panel): the set's narrowing
    // is lifted so non-members can be ticked in, and every row shows where it stands.
    const [editingSetRaw, setEditingSet] = useState(false);
    const editingSet = editingSetRaw && setsOpen && !!activeSet;
    useEffect(() => setEditingSet(false), [activeSetId]);
    const membership = useMemo(
        () => (editingSet && view && activeSet ? setMembership(activeSet, view.hierarchy) : null),
        [editingSet, view, activeSet],
    );
    const display = useMemo(
        () =>
            view
                ? displayHierarchy(view.hierarchy, viewSettings, {
                      searchActive,
                      showHidden,
                      keep: bothKeep(keepForProvider, editingSet ? undefined : setFilter?.keep),
                  })
                : null,
        [view, viewSettings, searchActive, showHidden, keepForProvider, setFilter, editingSet],
    );
    const setEditing: SetEditing | null =
        editingSet && membership && activeSet && view
            ? {
                  name: activeSet.name,
                  state: membership,
                  onToggle: (id) => {
                      const sets = useTreeSetsStore.getState();
                      const state = membership(id);
                      if (state === "covered") return;
                      if (state === "member") {
                          void sets.removeMembers(activeSet.id, [id]);
                          return;
                      }
                      // Adding a branch takes in the members below it: they would only be covered twice.
                      const under = activeSet.members
                          .filter((m) => m.id !== id && (m.path.includes(id) || ancestorsOf(view.hierarchy, m.id).includes(id)))
                          .map((m) => m.id);
                      void (async () => {
                          if (under.length) await sets.removeMembers(activeSet.id, under);
                          await sets.addMembers(activeSet.id, membersForIds([id], view.hierarchy));
                      })();
                  },
                  // A member's own provider choice, on its row; shown only where there is a choice.
                  trailing:
                      view.contentProviders.length > 1
                          ? (id) => {
                                const m = activeSet.members.find((x) => x.id === id);
                                if (!m) return null;
                                const providers = view.contentProviders;
                                return (
                                    <span className="flex items-center gap-0.5 shrink-0">
                                        {providers.map((p) => {
                                            const on = loadsProvider(m, p);
                                            return (
                                                <button
                                                    key={p}
                                                    type="button"
                                                    title={`${on ? "Loads" : "Does not load"} ${pn(p)}'s geometry for ${m.label} -- click to switch\n${providerIdTitle(p)}`}
                                                    className={`px-1 rounded-sm border text-[9px] leading-[14px] ${
                                                        on ? "bg-emerald-800/80 border-emerald-600 text-white" : "border-gray-600 text-gray-500 line-through"
                                                    }`}
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        const own = new Set(m.providers ?? providers);
                                                        if (!own.delete(p)) own.add(p);
                                                        void useTreeSetsStore.getState().setProviders(activeSet.id, [
                                                            { memberId: id, providers: providers.every((q) => own.has(q)) ? null : [...own].sort() },
                                                        ]);
                                                    }}
                                                >
                                                    {pn(p)}
                                                </button>
                                            );
                                        })}
                                    </span>
                                );
                            }
                          : undefined,
              }
            : null;
    const topKinds = useMemo(
        () => (view ? [...new Set(view.hierarchy.roots.map((id) => view.hierarchy.byId.get(id)?.data.kind ?? ""))] : []),
        [view],
    );

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

    // Which providers can be asked for ONE node (`asset_node_request`), read off the live specs, and
    // which declare request options for this collection (`./assetsTab/useNodeRequests`).
    const isAdmin = useMeStore((s) => s.isAdmin);
    const { nodeRequests, collectionOptionProviders } = useProviderRequestSpecs(scope, collection);
    // Per node, so a request keeps its progress while the user looks at other rows.
    const nodeRequestsFor = useNodeRequests(view, scope, isAdmin, nodeRequests, () => loader.refresh(scope));

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
                <RequestCollection
                    scope={scope}
                    onPublished={(c) => void onPublished(c)}
                    onChanged={() => void loader.refresh(scope)}
                    // Every collection of every provider, so not on the toolbar of the tree's one.
                    belowRequest={<AttributeSearchSection />}
                />
                <ChangeRunsPanel
                    scope={scope}
                    collection={collection}
                    onMarks={setRunMarkOverride}
                    onSelectNode={(node) => select(node)}
                />
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
                        open={setsOpen}
                        pressed={!!activeSet}
                        onClick={() => setSetsOpen((o) => !o)}
                    >
                        <path d="M2.5 3.5h11M2.5 8h11M2.5 12.5h6M11 11v3M9.5 12.5h3" />
                    </IconButton>
                    <IconButton
                        label="Options — provider filter, row marks and legend, how the tree is drawn, and provider options"
                        open={optionsOpen}
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
                                          badge: providerFilter ? pn(providerFilter) : null,
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
                                                      <option key={p} value={p} title={providerIdTitle(p)}>
                                                          {pn(p)}
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
                            ...(collection && collectionOptionProviders.size > 0 && sourcesTabAccess(isAdmin).providerOptions
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
                        providers={view?.contentProviders ?? []}
                        onSetProviders={(id, choices) => void useTreeSetsStore.getState().setProviders(id, choices)}
                        loadControl={view && activeSet ? <SetLoad view={view} set={activeSet} scope={scope} requestMissing={requestMissing} /> : null}
                        editing={editingSet}
                        onEditing={setEditingSet}
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
                    <SourcesSearchBox value={searchTerm} onChange={setSearchTerm} className={CONTROL} />
                </div>
                {display && (display.hiddenRoots > 0 || viewSettings.outOfScope.size > 0 || display.rootFilterStoodDown || providerFilter || view?.hasChangeOwners || (view?.pending.length ?? 0) > 0) && (
                    <div className="px-2 pt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-gray-400 shrink-0">
                        {view && <ChangedByFilter view={view} selected={selected} onSelect={select} />}
                        {view && view.pending.length > 0 && (
                            <PendingChip
                                pending={view.pending}
                                unmergedSpines={view.unmergedSpines.length}
                                loadingSpines={levelLoading.size > 0}
                                onPlace={() => void loader.loadLevels(scope, view.unmergedSpines)}
                            />
                        )}
                        {providerFilter && (
                            <button
                                type="button"
                                className="rounded-full bg-blue-900/60 px-2 py-0.5 text-blue-100 hover:bg-blue-800"
                                title={`Only rows that are, or contain, geometry from this provider are drawn. Click to show every provider.\n${providerIdTitle(providerFilter)}`}
                                onClick={() => setProviderFilter("")}
                            >
                                Provider: {pn(providerFilter)} ×
                            </button>
                        )}
                        {viewSettings.rootKinds && !display.rootFilterStoodDown && !searchActive && (
                            <button
                                type="button"
                                className="rounded-full bg-gray-700/70 px-2 py-0.5 text-gray-200 hover:bg-gray-600"
                                title={
                                    "Only these kinds are drawn at the top level. Change it under Options ▸ View." +
                                    (display.hiddenRoots > 0
                                        ? `\n${display.hiddenRoots} top-level branch${display.hiddenRoots === 1 ? "" : "es"} of other kinds ${display.hiddenRoots === 1 ? "is" : "are"} not drawn.`
                                        : "")
                                }
                                onClick={() => showOptions("view")}
                            >
                                Top: {[...viewSettings.rootKinds].join(", ")}
                                {display.hiddenRoots > 0 && <span className="text-gray-400"> · {display.hiddenRoots} hidden</span>}
                            </button>
                        )}
                        {display.hiddenRoots > 0 && (!viewSettings.rootKinds || display.rootFilterStoodDown || searchActive) && (
                            <span title="Top-level branches of other kinds are not drawn">{display.hiddenRoots} hidden</span>
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

                <div className="shrink-0">
                    {indexError && <Banner tone="error">{indexError}</Banner>}
                    {summary?.mixed && (
                        <Banner
                            tone="warn"
                            title={`This view spans ${summary.revisions.length} publishes:\n${summary.revisions.map(formatRevision).join("\n")}\nPick a run (beside the collection) for a coeval view.`}
                        >
                            Mixed: {summary.revisions.length} publishes, {formatRevision(summary.revisions[0])} …{" "}
                            {formatRevision(summary.revisions[summary.revisions.length - 1])}
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
                        <Banner tone="info" title={view.providers.map(providerIdTitle).join("\n")}>
                            Mixed collection — providers: {view.providers.map(pn).join(", ")}
                        </Banner>
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
                            editing={setEditing}
                        />
                    )}
                </div>
                {/* The box filters the rows held; the server's index finds the ones not loaded yet. */}
                {view && searchActive && (
                    <TreeServerHits scope={scope} collection={view.collection} term={searchTerm} held={(id) => view.hierarchy.byId.has(id)} />
                )}
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
