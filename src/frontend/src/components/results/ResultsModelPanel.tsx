/** Results-mode model panel — what the analysis contains, and how to isolate part of it.
 *
 *  The Results equivalent of Build mode's Model panel, laid out the way FE post-processors
 *  have presented a deck for decades (Xtract, Femap, Patran): totals at the top, the
 *  super-element breakdown under it, then the named sets with a search box.
 *
 *  Everything here reads from ``feaAnimationStore.manifest``. The bake already put both
 *  sections in it, so this panel introduces no store, no fetch and no new loading state —
 *  it renders what the loaded result already knows about itself.
 */

import React, {useCallback, useMemo, useRef, useState} from "react";

import {Checkbox, EmptyState, IconButton, Input, cn} from "@/components/ui";
import {Icon} from "@/components/icons";
import {applyFeaSetSelection, clearFeaSetSelection} from "@/shell/feaSetIsolation";
import {type FeaSet, filterSets, isElementSet, rangeBetween, selectedMemberCount, unionMembers} from "@/shell/feaSets";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";

const nf = new Intl.NumberFormat();

/** One labelled number in the header block. */
function Stat({label, value}: {label: string; value: string}) {
    return (
        <div className="flex flex-col gap-0.5">
            <span className="text-[10px] uppercase tracking-wide text-content-subtle">{label}</span>
            <span className="text-xs font-medium tabular-nums text-content">{value}</span>
        </div>
    );
}

/** Section heading, matching the Model panel's rhythm. */
function Heading({children, right}: {children: React.ReactNode; right?: React.ReactNode}) {
    return (
        <div className="flex items-center justify-between px-2 py-1 border-b border-edge bg-surface-2">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-content-muted">{children}</span>
            {right}
        </div>
    );
}

export default function ResultsModelPanel() {
    const manifest = useFeaAnimationStore((s) => s.manifest);
    const [query, setQuery] = useState("");
    const [selected, setSelected] = useState<ReadonlySet<string>>(() => new Set<string>());
    const [wireframeRest, setWireframeRest] = useState(true);
    // Shift-click anchor. A ref, not state: it must not trigger a re-render, and a stale
    // anchor after a filter change is harmless because rangeBetween re-resolves by name.
    const anchor = useRef<string | null>(null);

    const sets: FeaSet[] = useMemo(() => (manifest?.groups as FeaSet[] | undefined) ?? [], [manifest]);
    const visible = useMemo(() => filterSets(sets, query), [sets, query]);
    const info = manifest?.model_info;

    /** Push a selection to the scene. Always describes the full desired state — see
     *  applyFeaSetSelection on why this is not a delta. */
    const commit = useCallback(
        (next: ReadonlySet<string>, wire: boolean) => {
            setSelected(next);
            if (next.size === 0) {
                clearFeaSetSelection();
                return;
            }
            applyFeaSetSelection(unionMembers(sets, next), wire);
        },
        [sets],
    );

    const onRowClick = useCallback(
        (name: string, e: React.MouseEvent) => {
            const next = new Set(selected);
            if (e.shiftKey && anchor.current) {
                // Shift extends: add the span without clearing, so two ranges can be built up.
                for (const n of rangeBetween(visible, anchor.current, name)) next.add(n);
            } else if (e.ctrlKey || e.metaKey) {
                next.has(name) ? next.delete(name) : next.add(name);
                anchor.current = name;
            } else {
                // A plain click on the only selected row clears — otherwise there is no way
                // back to "everything" without hunting for the Clear button.
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
            setWireframeRest(on);
            if (selected.size > 0) applyFeaSetSelection(unionMembers(sets, selected), on);
        },
        [selected, sets],
    );

    const clear = useCallback(() => {
        anchor.current = null;
        setSelected(new Set());
        clearFeaSetSelection();
    }, []);

    if (!manifest) {
        return (
            <EmptyState
                title="No result loaded"
                hint="Open a result file — a Sesam .SIN, or a baked FEA model — to see its sets and super-elements."
            />
        );
    }

    // A node-only selection names no triangles, so ghosting the rest would blank the
    // viewport. Disable rather than let the toggle do something misleading.
    const selectedSets = sets.filter((s) => selected.has(s.name));
    const anyElements = selectedSets.some(isElementSet);
    const wireDisabled = selected.size > 0 && !anyElements;

    return (
        <div className="flex h-full min-h-0 flex-col text-content">
            {/* Model info */}
            <div className="grid grid-cols-2 gap-x-3 gap-y-2 border-b border-edge px-2 py-2">
                <Stat label="Nodes" value={info ? nf.format(info.n_nodes) : "—"} />
                <Stat label="Elements" value={info ? nf.format(info.n_elements) : "—"} />
            </div>

            {/* Super elements */}
            <Heading>Super elements</Heading>
            <div className="max-h-32 shrink-0 overflow-y-auto">
                {info && info.super_elements.length > 0 ? (
                    info.super_elements.map((se) => (
                        <div
                            key={se.index}
                            className="flex items-baseline justify-between gap-2 px-2 py-1 text-xs border-b border-edge/40 last:border-b-0"
                        >
                            <span className="truncate text-content">{se.name}</span>
                            <span className="shrink-0 tabular-nums text-[11px] text-content-subtle">
                                {se.n_nodes === null || se.n_elements === null
                                    ? "counts not per-SE"
                                    : `${nf.format(se.n_nodes)} n · ${nf.format(se.n_elements)} el`}
                            </span>
                        </div>
                    ))
                ) : (
                    <div className="px-2 py-2 text-xs text-content-subtle">Not reported by this result.</div>
                )}
            </div>

            {/* Sets */}
            <Heading
                right={
                    <div className="flex items-center gap-1">
                        <span className="text-[10px] tabular-nums text-content-subtle">
                            {selected.size > 0
                                ? `${selected.size} of ${sets.length}`
                                : `${sets.length}`}
                        </span>
                        <IconButton
                            icon={<Icon name="close" size="sm" />}
                            size="sm"
                            tooltip="Clear set selection"
                            disabled={selected.size === 0}
                            onClick={clear}
                        />
                    </div>
                }
            >
                Groups / sets
            </Heading>

            <div className="shrink-0 px-2 py-1.5">
                <Input
                    fieldSize="sm"
                    value={query}
                    placeholder="Search sets…"
                    aria-label="Search sets"
                    onChange={(e) => setQuery(e.target.value)}
                />
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto">
                {sets.length === 0 ? (
                    <div className="px-2 py-3 text-xs text-content-subtle">This result carries no named sets.</div>
                ) : visible.length === 0 ? (
                    <div className="px-2 py-3 text-xs text-content-subtle">No set matches “{query}”.</div>
                ) : (
                    visible.map((s) => {
                        const on = selected.has(s.name);
                        return (
                            <button
                                key={s.name}
                                type="button"
                                aria-pressed={on}
                                onClick={(e) => onRowClick(s.name, e)}
                                title={`${s.name} — ${nf.format(s.members.length)} ${isElementSet(s) ? "elements" : "nodes"}`}
                                className={cn(
                                    "flex w-full items-center gap-2 px-2 py-1 text-left text-xs",
                                    "pointer-fine:hover:bg-surface-3",
                                    on ? "bg-accent-subtle text-accent" : "text-content",
                                )}
                            >
                                <Icon
                                    name={isElementSet(s) ? "component" : "group"}
                                    size="sm"
                                    className="shrink-0 text-content-subtle"
                                />
                                <span className="min-w-0 flex-1 truncate">{s.name}</span>
                                <span className="shrink-0 tabular-nums text-[10px] text-content-subtle">
                                    {nf.format(s.members.length)}
                                </span>
                            </button>
                        );
                    })
                )}
            </div>

            {/* Isolation */}
            <div className="shrink-0 border-t border-edge px-2 py-1.5">
                <Checkbox
                    checked={wireframeRest && !wireDisabled}
                    disabled={wireDisabled}
                    onChange={(e) => toggleWire(e.target.checked)}
                    label="Show rest as wireframe"
                />
                {wireDisabled && (
                    <p className="mt-1 text-[10px] leading-tight text-content-subtle">
                        Node sets have no faces to isolate. Select an element set to use this.
                    </p>
                )}
                {selected.size > 0 && !wireDisabled && (
                    <p className="mt-1 text-[10px] leading-tight text-content-subtle tabular-nums">
                        {nf.format(selectedMemberCount(sets, selected))} members selected
                    </p>
                )}
            </div>
        </div>
    );
}
