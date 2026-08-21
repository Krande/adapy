/** The Outliner's Groups section — a result's named sets, and what they make visible.
 *
 *  Lives in the Outliner rather than a Results-only panel because it answers the same
 *  question the tree above it does: what is in this model, and which part am I looking at.
 *  It is a sibling section rather than another tree root so it can carry its own header
 *  controls, scroll independently of a 2,461-element tree, and be resized by the drag
 *  handle above it.
 *
 *  Selection here is VISIBILITY, not highlight — see feaSetIsolation.
 */

import React, {useCallback, useMemo, useRef} from "react";

import {Checkbox, cn} from "@/components/ui";
import {applyFeaGroupVisibility, clearFeaGroupVisibility} from "@/shell/feaSetIsolation";
import {type FeaSet, isElementSet, selectedMemberCount, unionMembers} from "@/shell/feaSets";

const nf = new Intl.NumberFormat();

export interface OutlinerGroupsProps {
    sets: readonly FeaSet[];
    /** Shared with the tree: one search box filters both. */
    query: string;
    collapsed: boolean;
    onToggleCollapsed: () => void;
    selected: ReadonlySet<string>;
    onSelectedChange: (next: ReadonlySet<string>) => void;
    wireframeRest: boolean;
    onWireframeRestChange: (on: boolean) => void;
    /** Section height in px when expanded, driven by the splitter above. */
    height: number;
}

export function OutlinerGroups({
    sets,
    query,
    collapsed,
    onToggleCollapsed,
    selected,
    onSelectedChange,
    wireframeRest,
    onWireframeRestChange,
    height,
}: OutlinerGroupsProps) {
    // Shift-click anchor. A ref, not state: it must not trigger a re-render, and a stale
    // anchor after a filter change is harmless because the range re-resolves by name.
    const anchor = useRef<string | null>(null);

    // Substring, not prefix: Sesam set names are overwhelmingly compound
    // (Mini_area_dbl_btm), so typing the part you remember — "btm" — has to find it.
    const visible = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) return [...sets];
        return sets.filter((s) => s.name.toLowerCase().includes(q));
    }, [sets, query]);

    const selectedSets = useMemo(() => sets.filter((s) => selected.has(s.name)), [sets, selected]);
    const anyElements = selectedSets.some(isElementSet);
    // A node-only selection names no triangles, so ghosting the rest would blank the
    // viewport. Disable rather than let the toggle do something misleading.
    const wireDisabled = selected.size > 0 && !anyElements;

    const commit = useCallback(
        (next: ReadonlySet<string>, wire: boolean) => {
            onSelectedChange(next);
            if (next.size === 0) clearFeaGroupVisibility();
            else applyFeaGroupVisibility(unionMembers(sets, next), wire);
        },
        [sets, onSelectedChange],
    );

    const onRowClick = useCallback(
        (name: string, e: React.MouseEvent) => {
            const next = new Set(selected);
            if (e.shiftKey && anchor.current) {
                // Anchored on the VISIBLE list: a range must never quietly include rows
                // the search is hiding.
                const a = visible.findIndex((s) => s.name === anchor.current);
                const b = visible.findIndex((s) => s.name === name);
                if (a >= 0 && b >= 0) {
                    const [lo, hi] = a <= b ? [a, b] : [b, a];
                    for (const s of visible.slice(lo, hi + 1)) next.add(s.name);
                } else {
                    next.add(name);
                }
            } else if (e.ctrlKey || e.metaKey) {
                if (next.has(name)) next.delete(name);
                else next.add(name);
                anchor.current = name;
            } else {
                // A plain click on the only selected row clears it — otherwise there is no
                // way back to the whole model without hunting for a separate control.
                const only = next.size === 1 && next.has(name);
                next.clear();
                if (!only) next.add(name);
                anchor.current = name;
            }
            commit(next, wireframeRest);
        },
        [selected, visible, wireframeRest, commit],
    );

    const toggleWire = useCallback(
        (on: boolean) => {
            onWireframeRestChange(on);
            if (selected.size > 0) applyFeaGroupVisibility(unionMembers(sets, selected), on);
        },
        [selected, sets, onWireframeRestChange],
    );

    const clear = useCallback(() => {
        anchor.current = null;
        onSelectedChange(new Set());
        clearFeaGroupVisibility();
    }, [onSelectedChange]);

    return (
        <div className="flex min-h-0 shrink-0 flex-col" style={collapsed ? undefined : {height}}>
            <div className="flex shrink-0 items-center gap-1 border-y border-edge bg-surface-2 px-1 py-1">
                <button
                    type="button"
                    onClick={onToggleCollapsed}
                    aria-expanded={!collapsed}
                    className="flex min-w-0 flex-1 items-center gap-1 text-left text-[11px] font-semibold uppercase tracking-wide text-content-muted pointer-fine:hover:text-content"
                >
                    <span className="w-3 shrink-0 text-center">{collapsed ? "▶" : "▼"}</span>
                    <span className="truncate">Groups</span>
                    <span className="shrink-0 tabular-nums font-normal normal-case text-content-subtle">
                        {selected.size > 0 ? `${selected.size} of ${sets.length}` : sets.length}
                    </span>
                </button>
                {selected.size > 0 && (
                    <button
                        type="button"
                        onClick={clear}
                        className="shrink-0 rounded-sm px-1 text-[10px] text-content-muted pointer-fine:hover:bg-surface-3 pointer-fine:hover:text-content"
                        title="Clear group selection and show the whole model"
                    >
                        Show all
                    </button>
                )}
            </div>

            {!collapsed && (
                <>
                    <div className="shrink-0 px-1 py-1">
                        <Checkbox
                            checked={wireframeRest && !wireDisabled}
                            disabled={wireDisabled}
                            onChange={(e) => toggleWire(e.target.checked)}
                            label="Show rest as wireframe"
                        />
                        {wireDisabled && (
                            <p className="mt-0.5 pl-5 text-[10px] leading-tight text-content-subtle">
                                Node groups have no faces to isolate.
                            </p>
                        )}
                    </div>

                    <div className="min-h-0 flex-1 overflow-y-auto scrollbar">
                        {visible.length === 0 ? (
                            <p className="px-2 py-2 text-xs text-content-subtle">
                                No group matches “{query}”.
                            </p>
                        ) : (
                            visible.map((s) => {
                                const on = selected.has(s.name);
                                return (
                                    <button
                                        key={s.name}
                                        type="button"
                                        aria-pressed={on}
                                        onClick={(e) => onRowClick(s.name, e)}
                                        title={`${s.name} — ${nf.format(s.members.length)} ${
                                            isElementSet(s) ? "elements" : "nodes"
                                        }`}
                                        className={cn(
                                            "flex w-full items-center gap-2 rounded-sm px-2 py-0.5 text-left text-xs",
                                            // Either/or, never both. cn is a plain join, so a
                                            // hover background and a selected background are two
                                            // utilities for one property and the stylesheet's
                                            // order decides -- hover won, and a selected row lost
                                            // its colour the moment you pointed at it.
                                            on
                                                ? "bg-accent text-white"
                                                : "text-content pointer-fine:hover:bg-surface-3",
                                        )}
                                    >
                                        <span className="min-w-0 flex-1 truncate">{s.name}</span>
                                        <span
                                            className={cn(
                                                "shrink-0 tabular-nums text-[10px]",
                                                on ? "text-white/70" : "text-content-subtle",
                                            )}
                                        >
                                            {nf.format(s.members.length)}
                                            {!isElementSet(s) && " n"}
                                        </span>
                                    </button>
                                );
                            })
                        )}
                    </div>

                    {selected.size > 0 && !wireDisabled && (
                        <p className="shrink-0 px-2 py-0.5 text-[10px] tabular-nums text-content-subtle">
                            Showing {nf.format(selectedMemberCount(sets, selected))} of the model
                        </p>
                    )}
                </>
            )}
        </div>
    );
}
