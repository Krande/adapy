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
// ONE QUIET ROW, marks only where they carry something. Label, then the
// provider's kind as a short code, then a right-aligned count column, then one
// publish-state dot: filled = published at this row, ring = covered by a publish
// above or holding one below. Everything else is a word, and only when it says
// something is wrong or different:
//
//   dimmed    nothing at or below to deliver -- reduced opacity, and it SAYS so
//             in its title. Rendered, never hidden: "there is nothing here" is
//             an answer, a missing row is not.
//   gap       something below, no publish covers it -- amber `gap`.
//   stale     drawn from a spine the resolution moved past -- gray `stale`.
//             Fixed by Refresh.
//   drift     published against an older tree -- amber `older tree`.
//   behind    (change feed) the SOURCE moved after this root was published --
//             RED `behind`, never the same mark as `stale`: fixed only by a new
//             export, and Refresh does nothing for it. The feed's other answers
//             (current, not recorded, no feed) are the row's tooltip, not a
//             chip on every row.
//   evidence  (change feed) the sweep found THIS node added/modified/deleted --
//             a purple per-node letter, independent of the root's own state.
//
// TWO STYLES, the same facts: `outline` draws a folder or a cube and the kind
// as a code; `tiles` draws a coloured tile per kind (`@/assets/kindTile`).

import React, { useMemo, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import type { AssetView } from "@/assets/assetView";
import type { ChangeAction, ChangeState } from "@/assets/changes";
import { flattenVisible, type Hierarchy } from "@/assets/hierarchy";
import { kindTile } from "@/assets/kindTile";
import { isOutOfScope } from "@/assets/treeView";
import type { AssetNode } from "@/assets/types";
import { rowFacts, searchRows, type RowBadge } from "@/assets/rowFacts";
import { canFetchSpine, rowSpineState, type SpineSource } from "@/assets/spines";
import { useViewerStores } from "@/state/AdaViewerContext";
import type { AssetTreeStyle } from "@/state/assetBrowserStore";

import { formatRevision } from "./format";

const ROW_HEIGHT = 26;
/** Horizontal step per level. A level's guide line runs under its parent's
 *  chevron: 6px into the 12px chevron column. */
const INDENT = 14;
const GUIDE_AT = 6;

const DELIVERY_WORD: Record<string, string> = { mesh: "a mesh", build: "a build", none: "nothing" };

const BADGE_TITLE: Record<RowBadge["weight"], string> = {
    solid: "Published at this node",
    ghost: "Covered by a publish rooted above",
    below: "Published content beneath this node",
};

/** The one publish-state mark: filled when published here, a ring otherwise. */
const StateDot: React.FC<{ badge: RowBadge }> = ({ badge }) => (
    <span
        className={`w-1.5 h-1.5 rounded-full shrink-0 ${
            badge.weight === "solid"
                ? "bg-blue-400"
                : badge.weight === "below"
                  ? "ring-[1.5px] ring-inset ring-blue-400/80"
                  : "ring-[1.5px] ring-inset ring-gray-500"
        }`}
        title={`${BADGE_TITLE[badge.weight]} — delivers ${DELIVERY_WORD[badge.delivery] ?? badge.delivery} — ${badge.at} @ ${formatRevision(badge.revision)}`}
    />
);

const Word: React.FC<{ tone: "amber" | "gray" | "red"; title: string; children: React.ReactNode }> = ({ tone, title, children }) => (
    <span
        title={title}
        className={`shrink-0 font-mono text-[10px] leading-none ${
            tone === "amber" ? "text-amber-300" : tone === "red" ? "text-red-300" : "text-gray-400"
        }`}
    >
        {children}
    </span>
);

const CHANGE_TITLE: Record<ChangeState, string> = {
    behind: "The source moved after this root was published. Re-export to catch up -- Refresh will not fix this.",
    current: "The change feed covered this root and found nothing newer at the source.",
    "not-recorded": "The change feed has never covered this root -- nobody has looked, which is not the same as unchanged.",
    "no-feed": "This deployment has no change-feed database. Whether the source moved cannot be said.",
};

const EVIDENCE_LETTER: Record<ChangeAction, string> = { added: "+", modified: "~", deleted: "−" };

// Per-NODE evidence -- what the sweep found AT this row -- is a purple
// family, deliberately apart from the root-level red `behind`: a leaf the
// sweep flagged `modified` inside a root already marked `behind` would
// otherwise repaint the same fact twice in the same colour.
const EvidenceMark: React.FC<{ action: ChangeAction }> = ({ action }) => (
    <span
        title={`The change feed's sweep recorded this node as ${action}.`}
        className="inline-flex items-center justify-center rounded-sm text-[9px] leading-none font-bold w-3.5 h-3.5 shrink-0 bg-purple-700/80 text-purple-100"
    >
        {EVIDENCE_LETTER[action]}
    </span>
);

const Chevron: React.FC<{ open: boolean }> = ({ open }) => (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true" fill="currentColor">
        {open ? <path d="M2 3l3 4 3-4z" /> : <path d="M3 2l4 3-4 3z" />}
    </svg>
);

// A branch or a leaf -- the `outline` style's only glyph distinction. Core cannot
// read a provider's `kind`, so the kind is printed as a code beside the label.
// Hand-drawn SVG in `currentColor`: no icon package.
const NodeGlyph: React.FC<{ branch: boolean }> = ({ branch }) => (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" className="shrink-0" fill="currentColor" stroke="currentColor">
        {branch ? (
            <path d="M1.5 3.5h4.2l1.5 1.6h7.3v8.4h-13z" fillOpacity="0.18" strokeWidth="1.2" strokeLinejoin="round" />
        ) : (
            <g strokeWidth="1.1" strokeLinejoin="round">
                <path d="M8 1.8 13.6 5v6.2L8 14.4 2.4 11.2V5z" fillOpacity="0.18" />
                <path d="M2.4 5 8 8.2 13.6 5M8 8.2v6.2" fill="none" />
            </g>
        )}
    </svg>
);

// The `tiles` style's mark: the kind itself, two letters on its own colour.
const KindTileMark: React.FC<{ kind: string }> = ({ kind }) => {
    const tile = kindTile(kind);
    return (
        <span
            className="w-[18px] h-[18px] rounded-[5px] shrink-0 grid place-items-center font-mono text-[9px] font-semibold"
            style={{ background: tile.bg, color: tile.fg }}
            title={kind}
        >
            {tile.letters}
        </span>
    );
};

const AssetRow: React.FC<{
    view: AssetView;
    id: string;
    depth: number;
    hasChildren: boolean;
    expanded: boolean;
    selected: boolean;
    spine: ReturnType<typeof rowSpineState>;
    showProvider: boolean;
    /** Drawn although it is out of scope, because "show hidden" is on. */
    outOfScope: boolean;
    treeStyle: AssetTreeStyle;
    onToggle: () => void;
    onSelect: () => void;
    onRetry: () => void;
}> = ({ view, id, depth, hasChildren, expanded, selected, spine, showProvider, outOfScope, treeStyle, onToggle, onSelect, onRetry }) => {
    const facts = rowFacts(view, id);
    if (!facts) return null;
    const { node } = facts;
    const reasons = [
        facts.dimmed ? "nothing at or below this node to deliver" : null,
        facts.gap ? `${facts.uncovered} of ${facts.payload} leaf node(s) below are not covered by any publish` : null,
        facts.changeState && facts.changeState !== "behind" ? CHANGE_TITLE[facts.changeState] : null,
        outOfScope ? "out of scope" : null,
    ].filter(Boolean);
    const title = reasons.length ? `${node.label} — ${reasons.join("; ")}` : node.label;
    const branch = hasChildren || spine.deadEnd || !node.leaf;
    const indent = 4 + depth * INDENT;
    return (
        <div
            role="treeitem"
            aria-selected={selected}
            aria-expanded={hasChildren ? expanded : undefined}
            aria-level={depth + 1}
            onClick={onSelect}
            className={`relative flex items-center gap-1.5 h-full pr-2 cursor-pointer rounded whitespace-nowrap text-[13px] ${
                selected ? "bg-blue-500/20 text-white shadow-[inset_2px_0_0_var(--color-blue-400)]" : "text-gray-200 hover:bg-white/5"
            } ${outOfScope ? "opacity-45" : facts.dimmed ? "opacity-60" : ""}`}
            style={{ paddingLeft: indent }}
            title={title}
        >
            {/* One guide per ancestor level. Drawn per row because the list is flat:
                virtualisation leaves no nested container to border. */}
            {Array.from({ length: depth }, (_, d) => (
                <span
                    key={d}
                    aria-hidden="true"
                    className="absolute top-0 bottom-0 w-px bg-gray-700/70"
                    style={{ left: 4 + d * INDENT + GUIDE_AT }}
                />
            ))}
            <span
                className={`w-3 shrink-0 grid place-items-center ${selected ? "text-gray-100" : "text-gray-400"}`}
                onClick={(e) => {
                    e.stopPropagation();
                    if (spine.error) onRetry();
                    else if (hasChildren) onToggle();
                }}
            >
                {spine.loading ? (
                    <span className="text-[10px]">…</span>
                ) : spine.error ? (
                    <span title={`Could not fetch this branch: ${spine.error} (click to retry)`} className="text-red-300 text-xs">!</span>
                ) : hasChildren ? (
                    <Chevron open={expanded} />
                ) : null}
            </span>
            {treeStyle === "tiles" ? (
                <KindTileMark kind={node.kind} />
            ) : (
                <span className={selected ? "text-gray-100" : "text-gray-400"}>
                    <NodeGlyph branch={branch} />
                </span>
            )}
            <span className={`truncate min-w-0 flex-1 ${selected ? "font-medium" : ""} ${outOfScope ? "line-through" : ""}`}>
                {node.label}
            </span>
            {treeStyle === "outline" && node.kind && (
                <span className="shrink-0 font-mono text-[10px] uppercase tracking-wide text-gray-400">{node.kind}</span>
            )}
            {facts.evidenceMark && <EvidenceMark action={facts.evidenceMark} />}
            {facts.changeState === "behind" && (
                <Word tone="red" title={CHANGE_TITLE.behind}>
                    behind
                </Word>
            )}
            {facts.gap && (
                <Word tone="amber" title={`${facts.uncovered} leaf node(s) at or below are not covered by any publish`}>
                    gap
                </Word>
            )}
            {spine.deadEnd && (
                <Word tone="gray" title="Marked as a branch, but no published hierarchy holds its children">
                    no subtree
                </Word>
            )}
            {facts.freshness?.stale && (
                <Word
                    tone="gray"
                    title={`Drawn from ${formatRevision(facts.freshness.shownAt)}; the resolution now names ${formatRevision(facts.freshness.resolvedAt)}. Refresh to rebuild.`}
                >
                    stale
                </Word>
            )}
            {facts.drift && (
                <Word
                    tone="amber"
                    title={`Published against the ${formatRevision(facts.drift.publishedAgainst)} tree; the tree shown is ${formatRevision(facts.drift.shownFrom)}`}
                >
                    older tree
                </Word>
            )}
            {outOfScope && (
                <Word tone="gray" title="Out of scope for this collection in this scope — shown because Show hidden is on">
                    out
                </Word>
            )}
            {showProvider && (
                <Word tone="gray" title="Producing provider">
                    {node.provider}
                </Word>
            )}
            <span className="w-8 shrink-0 text-right font-mono text-[11px] tabular-nums text-gray-500">
                {hasChildren && !expanded && facts.payload > 0 ? facts.payload : ""}
            </span>
            <span className="w-2 shrink-0 grid place-items-center">{facts.badge && <StateDot badge={facts.badge} />}</span>
        </div>
    );
};

const AssetTree: React.FC<{
    view: AssetView;
    /** The hierarchy as DRAWN (`displayHierarchy`): kinds flattened, the top
     *  level filtered, out-of-scope branches removed unless shown. Every fact a
     *  row carries still comes from `view`. */
    display: Hierarchy<AssetNode>;
    /** Out-of-scope ids, to mark the rows drawn anyway when Show hidden is on. */
    outOfScope: ReadonlySet<string>;
    showHidden: boolean;
    onRetrySpine: (source: SpineSource) => void;
}> = ({ view, display, outOfScope, showHidden, onRetrySpine }) => {
    const { useAssetBrowserStore } = useViewerStores();
    const expanded = useAssetBrowserStore((s) => s.expanded);
    const selected = useAssetBrowserStore((s) => s.selected);
    const searchTerm = useAssetBrowserStore((s) => s.searchTerm);
    const spineLoaded = useAssetBrowserStore((s) => s.spineLoaded);
    const spineLoading = useAssetBrowserStore((s) => s.spineLoading);
    const spineErrors = useAssetBrowserStore((s) => s.spineErrors);
    const treeStyle = useAssetBrowserStore((s) => s.treeStyle);
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
            flattenVisible(display, open, {
                expandable: (id) =>
                    canFetchSpine(view.hierarchy.byId.get(id)?.data, view.spines.get(id) ?? null, spineLoaded),
                include: search?.include,
            }),
        [view, display, open, search, spineLoaded],
    );
    const markOut = showHidden && outOfScope.size > 0;

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
            <div className="p-3 text-xs text-gray-400">
                {search ? `Nothing matches "${searchTerm}".` : "No rows yet for this resolution."}
            </div>
        );
    }
    return (
        <div ref={scrollRef} role="tree" className="flex-1 min-h-0 overflow-auto scrollbar px-1.5 py-1.5" data-testid="asset-tree">
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
                                outOfScope={markOut && isOutOfScope(view.hierarchy, outOfScope, row.id)}
                                treeStyle={treeStyle}
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
