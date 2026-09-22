// The asset forest as a virtualised row list.
//
// VIRTUALISED BECAUSE IT HAS TO BE: one spine can be ~41k rows and expanding it
// is the normal way to use the tab. The flat row list is computed once per
// (view, expansion, search) by `flattenVisible`, and only the window on screen
// is rendered.
//
// A row receives the view and its id and nothing else (`rowFacts`), so every
// mark on it is one function of one derived object.
//
// FOUR WAYS A ROW CAN READ AS LESS THAN ORDINARY, kept visually apart:
//   dimmed   nothing at or below to deliver -- reduced opacity, and it SAYS so
//            in its title. Rendered, never hidden: "there is nothing here" is
//            an answer, a missing row is not.
//   gap      something below, no publish covers it -- an amber `gap` tag.
//   stale    drawn from a spine the resolution moved past -- a `stale` tag.
//   drift    published against an older tree -- an amber `older tree` tag.

import React, { useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import type { AssetView } from "@/assets/assetView";
import { flattenVisible } from "@/assets/hierarchy";
import { rowFacts, searchRows, type RowBadge } from "@/assets/rowFacts";
import { canFetchSpine, rowSpineState, type SpineSource } from "@/assets/spines";
import { useViewerStores } from "@/state/AdaViewerContext";

import { formatRevision } from "./format";

const ROW_HEIGHT = 22;

const BADGE_LETTER: Record<string, string> = { mesh: "M", build: "B", none: "·" };

const BADGE_TITLE: Record<RowBadge["weight"], string> = {
    solid: "Published at this node",
    ghost: "Covered by a publish rooted above",
    below: "Published content beneath this node",
};

const Badge: React.FC<{ badge: RowBadge }> = ({ badge }) => {
    const cls =
        badge.weight === "solid"
            ? "bg-blue-500 text-white"
            : badge.weight === "below"
              ? "bg-blue-500/25 text-blue-200 ring-1 ring-inset ring-blue-400"
              : "text-gray-300 ring-1 ring-inset ring-gray-500";
    return (
        <span
            className={`ml-1 inline-flex items-center justify-center rounded-sm text-[9px] leading-none font-bold w-3.5 h-3.5 shrink-0 ${cls}`}
            title={`${BADGE_TITLE[badge.weight]} (${badge.delivery}) — ${badge.at} @ ${formatRevision(badge.revision)}`}
        >
            {BADGE_LETTER[badge.delivery] ?? "?"}
        </span>
    );
};

const Tag: React.FC<{ tone: "amber" | "gray"; title: string; children: React.ReactNode }> = ({ tone, title, children }) => (
    <span
        title={title}
        className={`ml-1 shrink-0 rounded-sm px-1 text-[9px] leading-[14px] ${
            tone === "amber" ? "bg-amber-800/70 text-amber-100" : "bg-gray-600 text-gray-200"
        }`}
    >
        {children}
    </span>
);

const AssetRow: React.FC<{
    view: AssetView;
    id: string;
    depth: number;
    hasChildren: boolean;
    expanded: boolean;
    selected: boolean;
    spine: ReturnType<typeof rowSpineState>;
    showProvider: boolean;
    onToggle: () => void;
    onSelect: () => void;
    onRetry: () => void;
}> = ({ view, id, depth, hasChildren, expanded, selected, spine, showProvider, onToggle, onSelect, onRetry }) => {
    const facts = rowFacts(view, id);
    if (!facts) return null;
    const { node } = facts;
    const title = facts.dimmed
        ? `${node.label} — nothing at or below this node to deliver`
        : facts.gap
          ? `${node.label} — ${facts.uncovered} of ${facts.payload} leaf node(s) below are not covered by any publish`
          : node.label;
    return (
        <div
            role="treeitem"
            aria-selected={selected}
            aria-expanded={hasChildren ? expanded : undefined}
            onClick={onSelect}
            className={`flex items-center h-full pr-1 cursor-pointer rounded-sm whitespace-nowrap text-sm ${
                selected ? "bg-blue-700" : "hover:bg-gray-700"
            } ${facts.dimmed ? "opacity-60" : ""}`}
            style={{ paddingLeft: 4 + depth * 12 }}
            title={title}
        >
            <span
                className="w-4 shrink-0 text-center text-xs text-gray-300"
                onClick={(e) => {
                    e.stopPropagation();
                    if (spine.error) onRetry();
                    else if (hasChildren) onToggle();
                }}
            >
                {spine.loading ? "…" : spine.error ? <span title={`Could not fetch this branch: ${spine.error} (click to retry)`} className="text-red-300">!</span> : hasChildren ? (expanded ? "▼" : "▶") : ""}
            </span>
            <span className="truncate">{node.label}</span>
            {node.kind && <span className="ml-1 text-[10px] text-gray-400 truncate">{node.kind}</span>}
            {hasChildren && !expanded && facts.payload > 0 && (
                <span className="ml-1 text-[10px] text-gray-500">{facts.payload}</span>
            )}
            {facts.badge && <Badge badge={facts.badge} />}
            {facts.gap && <Tag tone="amber" title={`${facts.uncovered} leaf node(s) at or below are not covered by any publish`}>gap</Tag>}
            {spine.deadEnd && (
                <Tag tone="gray" title="Marked as a branch, but no published hierarchy holds its children">no subtree</Tag>
            )}
            {facts.freshness?.stale && (
                <Tag
                    tone="gray"
                    title={`Drawn from ${formatRevision(facts.freshness.shownAt)}; the resolution now names ${formatRevision(facts.freshness.resolvedAt)}. Refresh to rebuild.`}
                >
                    stale
                </Tag>
            )}
            {facts.drift && (
                <Tag
                    tone="amber"
                    title={`Published against the ${formatRevision(facts.drift.publishedAgainst)} tree; the tree shown is ${formatRevision(facts.drift.shownFrom)}`}
                >
                    older tree
                </Tag>
            )}
            {showProvider && <Tag tone="gray" title="Producing provider">{node.provider}</Tag>}
        </div>
    );
};

const AssetTree: React.FC<{ view: AssetView; onRetrySpine: (source: SpineSource) => void }> = ({ view, onRetrySpine }) => {
    const { useAssetBrowserStore } = useViewerStores();
    const expanded = useAssetBrowserStore((s) => s.expanded);
    const selected = useAssetBrowserStore((s) => s.selected);
    const searchTerm = useAssetBrowserStore((s) => s.searchTerm);
    const spineLoaded = useAssetBrowserStore((s) => s.spineLoaded);
    const spineLoading = useAssetBrowserStore((s) => s.spineLoading);
    const spineErrors = useAssetBrowserStore((s) => s.spineErrors);
    const { toggleExpanded, select } = useAssetBrowserStore.getState();

    const search = useMemo(() => searchRows(view.hierarchy, searchTerm), [view.hierarchy, searchTerm]);
    const open = useMemo(() => {
        if (!search) return expanded;
        const s = new Set(expanded);
        for (const id of search.open) s.add(id);
        return s;
    }, [expanded, search]);

    const rows = useMemo(
        () =>
            flattenVisible(view.hierarchy, open, {
                expandable: (id) =>
                    canFetchSpine(view.hierarchy.byId.get(id)?.data, view.spines.get(id) ?? null, spineLoaded),
                include: search?.include,
            }),
        [view, open, search, spineLoaded],
    );

    const scrollRef = useRef<HTMLDivElement | null>(null);
    const virtualizer = useVirtualizer({
        count: rows.length,
        getScrollElement: () => scrollRef.current,
        estimateSize: () => ROW_HEIGHT,
        overscan: 12,
    });
    const showProvider = view.providers.length > 1;

    if (!rows.length) {
        return (
            <div className="p-2 text-xs text-gray-400">
                {search ? `Nothing matches "${searchTerm}".` : "No rows yet for this resolution."}
            </div>
        );
    }
    return (
        <div ref={scrollRef} role="tree" className="flex-1 min-h-0 overflow-auto scrollbar px-1 pt-1" data-testid="asset-tree">
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                {virtualizer.getVirtualItems().map((item) => {
                    const row = rows[item.index];
                    const source = view.spines.get(row.id) ?? null;
                    const spine = rowSpineState({
                        node: view.hierarchy.byId.get(row.id)?.data,
                        hasChildren: view.hierarchy.childrenOf(row.id).length > 0,
                        source,
                        loaded: spineLoaded,
                        loading: spineLoading,
                        errors: spineErrors,
                    });
                    return (
                        <div
                            key={row.id}
                            style={{ position: "absolute", top: 0, left: 0, right: 0, height: ROW_HEIGHT, transform: `translateY(${item.start}px)` }}
                        >
                            <AssetRow
                                view={view}
                                id={row.id}
                                depth={row.depth}
                                hasChildren={row.hasChildren}
                                expanded={row.expanded}
                                selected={selected === row.id}
                                spine={spine}
                                showProvider={showProvider}
                                onToggle={() => toggleExpanded(row.id)}
                                onSelect={() => select(row.id)}
                                onRetry={() => source && onRetrySpine(source)}
                            />
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

export default AssetTree;
