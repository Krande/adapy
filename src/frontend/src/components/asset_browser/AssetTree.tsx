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

import React, { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

import { Chevron, NodeGlyph } from "@/components/tree_view/treeGlyphs";
import type { SetMembership } from "@/assets/treeSets";

import type { AssetView } from "@/assets/assetView";
import type { ChangeAction, ChangeState } from "@/assets/changes";
import { ancestorsOf, flattenVisible, type Hierarchy } from "@/assets/hierarchy";
import { kindTile } from "@/assets/kindTile";
import { providerIdTitle } from "@/assets/providerNames";
import { useProviderName } from "@/state/providerNamesStore";
import { isOutOfScope } from "@/assets/treeView";
import type { AssetNode } from "@/assets/types";
import { isSearchTerm, rowFacts, searchRows, shallowestHit, type RowBadge } from "@/assets/rowFacts";
import { levelWanted, rowLevelState, type LevelRequest } from "@/assets/spines";
import { rangeIds, treeKeyAction } from "@/assets/treeKeys";
import { useViewerStores } from "@/state/AdaViewerContext";
import type { AssetTreeMarks, AssetTreeStyle } from "@/state/assetBrowserStore";
import { GEOMETRY_MARK_TITLE, geometryIndex, geometryMark, type GeometryIndex, type GeometryMark } from "@/assets/geometryMarks";

import { formatRevision } from "./format";
import {closeTreeFromKeyboard, isTreeCloseKey} from "@/utils/tree_view/treeKeyboard";

/** How a row is being chosen: alone (click), from the anchor to it (shift-click), in or out of
 *  the selection (ctrl/cmd-click), or focused within the selection it is already part of
 *  (right-click). */
type SelectHow = "only" | "range" | "toggle" | "keep";

const ROW_HEIGHT = 26;
const SEARCH_DEBOUNCE_MS = 180;
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

/** A node with content from several providers: one mark per provider, in the same filled / ring
 *  language as `StateDot`, tinted by provider so the marks can be told apart. The tint is hashed
 *  from the provider id (`kindTile`), so core names no provider and a provider keeps its colour. */
const ProviderDots: React.FC<{ claims: readonly RowBadge[] }> = ({ claims }) => {
    const pn = useProviderName();
    return (
    <span className="shrink-0 flex items-center gap-0.5">
        {claims.map((c) => {
            const color = kindTile(c.provider).bg;
            return (
                <span
                    key={c.provider}
                    className="w-1.5 h-1.5 rounded-full"
                    style={
                        c.weight === "solid"
                            ? { background: color }
                            : { boxShadow: `inset 0 0 0 1.5px ${color}`, opacity: c.weight === "below" ? 0.8 : 0.6 }
                    }
                    title={`${pn(c.provider)}: ${BADGE_TITLE[c.weight]} — delivers ${DELIVERY_WORD[c.delivery] ?? c.delivery} — ${c.at} @ ${formatRevision(c.revision)}\n${providerIdTitle(c.provider)}`}
                />
            );
        })}
    </span>
    );
};

/** The geometry overlay's mark: green = there is something to load (filled here, ring covered from
 *  above, faint ring somewhere below); gray ring = tree only; a dotted ring = not known yet. */
export const GeometryDot: React.FC<{ mark: GeometryMark | null }> = ({ mark }) => {
    if (!mark) return null;
    const style: React.CSSProperties =
        mark === "here"
            ? { background: GEOMETRY_GREEN }
            : mark === "covered"
              ? { boxShadow: `inset 0 0 0 1.5px ${GEOMETRY_GREEN}` }
              : mark === "below"
                ? { boxShadow: `inset 0 0 0 1.5px ${GEOMETRY_GREEN}`, opacity: 0.55 }
                : mark === "tree"
                  ? { boxShadow: "inset 0 0 0 1.5px #6b7280" }
                  : { border: "1px dotted #9ca3af" };
    return <span className="w-1.5 h-1.5 rounded-full shrink-0 inline-block" style={style} title={GEOMETRY_MARK_TITLE[mark]} />;
};

export const GEOMETRY_GREEN = "#34d399";

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

/** The producing provider, by its display name; the id on hover. */
const ProviderWord: React.FC<{ provider: string }> = ({ provider }) => {
    const pn = useProviderName();
    return (
        <Word tone="gray" title={`Producing provider\n${providerIdTitle(provider)}`}>
            {pn(provider)}
        </Word>
    );
};

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

// `NodeGlyph` (a branch or a leaf) is the `outline` style's only glyph distinction. Core cannot
// read a provider's `kind`, so the kind is printed as a code beside the label.

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

/** Editing a set in the tree itself (the Sets panel open on a set): each row shows whether it is a
 *  member and toggles it, instead of a second list of the same rows in the panel. */
export interface SetEditing {
    name: string;
    state: (id: string) => SetMembership;
    onToggle: (id: string) => void;
    /** Extra marks on a member row -- whose geometry it loads. */
    trailing?: (id: string) => React.ReactNode;
}

const MEMBER_COLUMN = 18;

const MEMBER_TITLE: Record<NonNullable<SetMembership> | "none", (set: string) => string> = {
    member: (s) => `In "${s}" -- click to take it out`,
    covered: (s) => `In "${s}" through a branch above it`,
    contains: (s) => `Holds members of "${s}" below -- click to add the whole branch`,
    none: (s) => `Not in "${s}" -- click to add it (and everything under it)`,
};

const MemberBox: React.FC<{ state: SetMembership; setName: string; onToggle: () => void }> = ({ state, setName, onToggle }) => (
    <button
        type="button"
        role="checkbox"
        aria-checked={state === "member" || state === "covered" ? true : state === "contains" ? "mixed" : false}
        disabled={state === "covered"}
        title={MEMBER_TITLE[state ?? "none"](setName)}
        onClick={(e) => {
            e.stopPropagation();
            onToggle();
        }}
        className={`absolute left-1 top-1/2 -translate-y-1/2 w-3 h-3 rounded-[3px] border grid place-items-center text-[9px] leading-none ${
            state === "member"
                ? "bg-emerald-500 border-emerald-400 text-gray-950"
                : state === "covered"
                  ? "border-emerald-600/70 text-emerald-400/70 cursor-default"
                  : state === "contains"
                    ? "border-emerald-500 text-emerald-300 hover:bg-emerald-500/20"
                    : "border-gray-500 hover:border-emerald-400"
        }`}
    >
        {state === "member" || state === "covered" ? "✓" : state === "contains" ? "–" : ""}
    </button>
);

const AssetRow: React.FC<{
    view: AssetView;
    id: string;
    depth: number;
    hasChildren: boolean;
    expanded: boolean;
    /** In the selection. */
    selected: boolean;
    /** THE row: the one the keys move from and the detail shows. */
    focused: boolean;
    spine: ReturnType<typeof rowLevelState>;
    showProvider: boolean;
    /** Drawn although it is out of scope, because "show hidden" is on. */
    outOfScope: boolean;
    treeStyle: AssetTreeStyle;
    /** What the row's trailing marks show (`AssetTreeMarks`). */
    marks: AssetTreeMarks;
    /** The geometry index, when `marks` is "geometry". */
    geoIdx: GeometryIndex | null;
    onToggle: () => void;
    onSelect: (how: SelectHow) => void;
    onRetry: () => void;
    onContextMenu: (x: number, y: number) => void;
    /** While a set is being edited: this row's standing in it, and the controls for it. */
    editing?: SetEditing | null;
}> = ({ view, id, depth, hasChildren, expanded, selected, focused, spine, showProvider, outOfScope, treeStyle, marks, geoIdx, onToggle, onSelect, onRetry, onContextMenu, editing }) => {
    const facts = rowFacts(view, id);
    if (!facts) return null;
    const membership = editing ? editing.state(id) : null;
    const { node } = facts;
    const reasons = [
        facts.dimmed ? "nothing at or below this node to deliver" : null,
        facts.gap ? `${facts.uncovered} of ${facts.payload} leaf node(s) below are not covered by any publish` : null,
        facts.changeState && facts.changeState !== "behind" ? CHANGE_TITLE[facts.changeState] : null,
        outOfScope ? "out of scope" : null,
    ].filter(Boolean);
    const title = reasons.length ? `${node.label} — ${reasons.join("; ")}` : node.label;
    const branch = hasChildren || spine.deadEnd || !node.leaf;
    // The membership column, when a set is edited, sits left of everything and shifts the rest.
    const column = editing ? MEMBER_COLUMN : 0;
    const indent = 4 + column + depth * INDENT;
    const tint = selected ? "" : membership === "member" ? "bg-emerald-500/15" : membership === "covered" ? "bg-emerald-500/[0.07]" : "";
    return (
        <div
            role="treeitem"
            aria-selected={selected}
            aria-expanded={hasChildren ? expanded : undefined}
            aria-level={depth + 1}
            // Shift-click would otherwise also drag a text selection across the rows it spans.
            onMouseDown={(e) => e.shiftKey && e.preventDefault()}
            onClick={(e) => onSelect(e.shiftKey ? "range" : e.ctrlKey || e.metaKey ? "toggle" : "only")}
            onContextMenu={(e) => {
                e.preventDefault();
                // Focus first, so the detail below shows the row the menu acts on -- keeping a
                // selection the row is part of, as a file manager does.
                onSelect("keep");
                onContextMenu(e.clientX, e.clientY);
            }}
            className={`relative flex items-center gap-1.5 h-full pr-2 cursor-pointer rounded whitespace-nowrap text-[13px] ${
                selected ? "bg-blue-500/20 text-white" : "text-gray-200 hover:bg-white/5"
            } ${tint} ${focused ? "shadow-[inset_2px_0_0_var(--color-blue-400)]" : ""} ${outOfScope ? "opacity-45" : facts.dimmed ? "opacity-60" : ""}`}
            style={{ paddingLeft: indent }}
            title={title}
        >
            {editing && <MemberBox state={membership} setName={editing.name} onToggle={() => editing.onToggle(id)} />}
            {/* One guide per ancestor level. Drawn per row because the list is flat:
                virtualisation leaves no nested container to border. */}
            {Array.from({ length: depth }, (_, d) => (
                <span
                    key={d}
                    aria-hidden="true"
                    className="absolute top-0 bottom-0 w-px bg-gray-700/70"
                    style={{ left: 4 + column + d * INDENT + GUIDE_AT }}
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
            {membership === "member" && editing?.trailing?.(id)}
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
            {showProvider && <ProviderWord provider={node.provider} />}
            <span className="w-8 shrink-0 text-right font-mono text-[11px] tabular-nums text-gray-500">
                {/* Not on an unexplored branch: a level below is still unfetched, so
                    the leaves held there are a floor, not a count. */}
                {hasChildren && !expanded && facts.payload > 0 && !view.unexplored.has(id) ? facts.payload : ""}
            </span>
            {marks === "providers" ? (
                // Always tinted by provider -- one claim or several -- so a colour means one
                // provider everywhere; the Legend names them.
                facts.claims.length ? (
                    <ProviderDots claims={facts.claims} />
                ) : (
                    <span className="w-2 shrink-0 grid place-items-center">{facts.badge && <StateDot badge={facts.badge} />}</span>
                )
            ) : marks === "geometry" && geoIdx ? (
                <span className="w-2 shrink-0 grid place-items-center">
                    <GeometryDot mark={geometryMark(view, geoIdx, id)} />
                </span>
            ) : null}
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
    onRetryLevel: (req: LevelRequest) => void;
    /** Right-click on a row, at viewport coordinates. */
    onRowContextMenu: (id: string, x: number, y: number) => void;
    /** The provider the tree is filtered to, if any. The geometry overlay then answers for THAT
     *  provider: marking every row with another provider's geometry under a filter that says "show
     *  me this provider" reads as this provider having it. */
    geometryProvider?: string | null;
    /** A set being edited in the tree: a membership column on every row. */
    editing?: SetEditing | null;
}> = ({ view, display, outOfScope, showHidden, onRetryLevel, onRowContextMenu, geometryProvider, editing }) => {
    const { useAssetBrowserStore } = useViewerStores();
    const expanded = useAssetBrowserStore((s) => s.expanded);
    const selected = useAssetBrowserStore((s) => s.selected);
    const typedTerm = useAssetBrowserStore((s) => s.searchTerm);
    // Searched once typing pauses, not per keystroke: a search over a whole project is tens of
    // milliseconds, and running it between keystrokes is what made the field stutter. Dropping a
    // search is immediate -- there is nothing to compute, and a stale filter would linger.
    const [searchTerm, setSearchTerm] = useState(typedTerm);
    useEffect(() => {
        if (!isSearchTerm(typedTerm)) {
            setSearchTerm(typedTerm);
            return;
        }
        const t = setTimeout(() => setSearchTerm(typedTerm), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(t);
    }, [typedTerm]);
    const levelLoaded = useAssetBrowserStore((s) => s.levelLoaded);
    const levelLoading = useAssetBrowserStore((s) => s.levelLoading);
    const levelErrors = useAssetBrowserStore((s) => s.levelErrors);
    const treeStyle = useAssetBrowserStore((s) => s.treeStyle);
    const treeMarks = useAssetBrowserStore((s) => s.treeMarks);
    // The server's roll-up, when it was fetched: what lets an unopened branch be marked at all.
    const geometryRollup = useAssetBrowserStore((s) => s.geometryRollup);
    // Built once per view, only when the geometry overlay is on.
    const geoIdx = useMemo(
        () => (treeMarks === "geometry" ? geometryIndex(view, geometryProvider || undefined, geometryRollup) : null),
        [view, treeMarks, geometryProvider, geometryRollup],
    );
    const selection = useAssetBrowserStore((s) => s.selection);
    const { toggleExpanded, setExpanded, select, selectRange, toggleSelected } = useAssetBrowserStore.getState();

    const search = useMemo(() => searchRows(view.hierarchy, searchTerm), [view.hierarchy, searchTerm]);
    const open = useMemo(() => {
        if (!search) return expanded;
        const s = new Set(expanded);
        for (const id of search.open) s.add(id);
        return s;
    }, [expanded, search]);

    // While searching, siblings are ordered by their shallowest hit: a hit at the top level lists
    // before a branch whose hits are deeper, whatever the tree's own order.
    const hitDepth = useMemo(() => (search ? shallowestHit(display, search.hits) : null), [display, search]);
    const rows = useMemo(
        () =>
            flattenVisible(display, open, {
                // Asked only of a row with nothing under it (as drawn): can it
                // open anyway -- a level still to fetch below it?
                expandable: (id) =>
                    levelWanted(
                        view.hierarchy.byId.get(id)?.data,
                        view.levelOf(id),
                        levelLoaded,
                        view.hierarchy.childrenOf(id).length > 0,
                    ),
                include: search?.include,
                rank: hitDepth ? (id) => hitDepth.get(id) ?? Infinity : undefined,
            }),
        [view, display, open, search, hitDepth, levelLoaded],
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

    // A row revealed from outside the tree (a pick in the 3D view, `revealPickInTrees`): scroll to
    // it once it is among the drawn rows. The reveal opens its branch in the same update, so it is
    // normally there on the next render; a row the drawing hides (a flattened kind, a search, out
    // of scope) falls back to its nearest drawn ancestor. Not drawn at all yet: wait for the rows.
    const reveal = useAssetBrowserStore((s) => s.reveal);
    useEffect(() => {
        if (!reveal) return;
        let at = rows.findIndex((r) => r.id === reveal.id);
        if (at < 0) {
            for (const up of ancestorsOf(view.hierarchy, reveal.id)) {
                at = rows.findIndex((r) => r.id === up);
                if (at >= 0) break;
            }
        }
        if (at < 0) return;
        virtualizer.scrollToIndex(at, { align: "center" });
        useAssetBrowserStore.getState().clearReveal(reveal.nonce);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [reveal, rows]);

    // The keys read "open" as the user's own expansion: a branch a search holds open is closed as
    // far as Left is concerned, which steps to its parent rather than doing nothing.
    const keyRows = useMemo(
        () => rows.map((r) => ({ id: r.id, depth: r.depth, hasChildren: r.hasChildren, expanded: expanded.has(r.id) })),
        [rows, expanded],
    );

    /** Select `id` the way a click (or the context menu) asks, and keep keyboard focus in the tree. */
    const chooseRow = (id: string, how: SelectHow) => {
        const s = useAssetBrowserStore.getState();
        if (how === "range") selectRange(rangeIds(rows, s.anchor ?? s.selected, id), id);
        else if (how === "toggle") toggleSelected(id);
        else if (how === "keep" && s.selection.has(id)) selectRange([...s.selection], id);
        else select(id);
        scrollRef.current?.focus({ preventScroll: true });
    };

    const onKeyDown = (e: React.KeyboardEvent) => {
        const s = useAssetBrowserStore.getState();
        if (isTreeCloseKey(e)) {
            e.preventDefault();
            // Stopped, or Alt+T would go on to the viewer's handler and reopen the drawer.
            e.stopPropagation();
            // Esc first narrows a multi-selection back to the focused row; once there is nothing
            // to narrow, it (and Alt+T always) closes the drawer.
            if (e.key === "Escape" && s.selected && s.selection.size > 1) select(s.selected);
            else closeTreeFromKeyboard();
            return;
        }
        if (e.altKey || e.ctrlKey || e.metaKey) return;
        const action = treeKeyAction(keyRows, s.selected, e.key, e.shiftKey);
        if (!action) return;
        e.preventDefault();
        if (action.kind === "expand") setExpanded(action.id, true);
        else if (action.kind === "collapse") setExpanded(action.id, false);
        else {
            if (action.extend) selectRange(rangeIds(rows, s.anchor ?? s.selected, action.id), action.id);
            else select(action.id);
            virtualizer.scrollToIndex(rows.findIndex((r) => r.id === action.id), { align: "auto" });
        }
    };

    if (!rows.length) {
        return (
            <div className="p-3 text-xs text-gray-400">
                {search ? `Nothing matches "${searchTerm}".` : "No rows yet for this resolution."}
            </div>
        );
    }
    return (
        <div
            ref={scrollRef}
            role="tree"
            aria-multiselectable="true"
            tabIndex={0}
            onKeyDown={onKeyDown}
            className="flex-1 min-h-0 overflow-auto scrollbar px-1.5 py-1.5 outline-none"
            data-testid="asset-tree"
        >
            <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
                {virtualizer.getVirtualItems().map((item) => {
                    const row = rows[item.index];
                    const level = view.levelOf(row.id);
                    const spine = rowLevelState({
                        node: view.hierarchy.byId.get(row.id)?.data,
                        hasChildren: view.hierarchy.childrenOf(row.id).length > 0,
                        req: level,
                        loaded: levelLoaded,
                        loading: levelLoading,
                        errors: levelErrors,
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
                                selected={selection.has(row.id)}
                                focused={selected === row.id}
                                spine={spine}
                                showProvider={showProvider}
                                outOfScope={markOut && isOutOfScope(view.hierarchy, outOfScope, row.id)}
                                treeStyle={treeStyle}
                                marks={treeMarks}
                                geoIdx={geoIdx}
                                onToggle={() => toggleExpanded(row.id)}
                                onSelect={(how) => chooseRow(row.id, how)}
                                onRetry={() => level && onRetryLevel(level)}
                                onContextMenu={(x, y) => onRowContextMenu(row.id, x, y)}
                                editing={editing}
                            />
                        </div>
                    );
                })}
            </div>
        </div>
    );
};

export default AssetTree;
