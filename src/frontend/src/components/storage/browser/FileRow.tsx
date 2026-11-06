import React, {useEffect, useRef} from "react";
import type {ServerFileEntry} from "@/state/serverInfoStore";
import {runtime} from "@/runtime/config";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";
import InlineNameInput from "@/components/common/InlineNameInput";
import type {KebabMenuItem} from "@/components/common/PositionedMenu";
import FileTypeIcon from "../../icons/FileTypeIcon";
import ViewIcon from "../../icons/ViewIcon";
import {canOpenInScene, isFEAResult, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {localDateTime} from "@/utils/time";
import {Spinner, formatRelative, parseLastModifiedMs} from "./helpers";
import {claimLoad} from "@/plugins/loadClaims";
import type {RowLoadState} from "./rowLoadProgress";

/** A row's load told on the row: its state and the row's Cancel and Dismiss. */
export interface RowLoadProgress {
    state: RowLoadState;
    onCancel: () => void;
    onDismiss: () => void;
}

// FileRow: one storage entry, optionally indented (for use inside
// the per-commit subtree). Pulled out of the main component so the
// versions tree can render the same row at indent 2 without
// re-implementing the toggle/expand/spinner machinery.
// ──────────────────────────────────────────────────────────────────
export interface FileRowProps {
    file: ServerFileEntry;
    displayName: string;
    indentLevel: number;
    viewingName: string | null;
    loadedSourceNames: ReadonlySet<string>;
    conversionJobs: Record<string, {progress: number; status?: string}>;
    expandedName: string | null;
    setExpandedName: (n: string | null) => void;
    onToggle: (entry: ServerFileEntry, nextChecked: boolean) => Promise<void>;
    setPickerName: (n: string | null) => void;
    isSelected: boolean;
    /** Waiting in the scene-load queue (untick to remove). */
    isQueued?: boolean;
    onSelectToggle: (name: string, shiftKey?: boolean) => void;
    /** Row actions — shared between the kebab and the right-click
     * context menu (parent builds both from one list). */
    menuItems: KebabMenuItem[];
    /** Desktop right-click AND touch long-press both land here. */
    onOpenContextMenu?: (e: {clientX: number; clientY: number; preventDefault?: () => void; stopPropagation?: () => void}) => void;
    /** Keyboard-navigation identity + highlight. */
    rowKey?: string;
    focused?: boolean;
    /** In-panel drag source (move to folder). */
    draggable?: boolean;
    onDragStartRow?: (e: React.DragEvent) => void;
    onDragEndRow?: () => void;
    /** OS-file drops on this row (upload into the row's folder). */
    onDropAt?: (e: React.DragEvent) => void;
    /** Row is part of the in-flight drag payload. */
    dimmed?: boolean;
    renaming?: boolean;
    onRenameCommit?: (newBasename: string) => void;
    onRenameCancel?: () => void;
    /** Show when the file last changed. On by default from the file tree.
     *
     *  IT USED TO BE MAXIMIZED-ONLY, for horizontal space, and the cost of
     *  that was the question it exists to answer: given `review/x.glb` and
     *  `review/x-v9.glb`, which one is current? Nothing else in the row says.
     *  Sixty pixels of a truncating filename is a cheap price for not having
     *  to open a second panel to find out.
     *
     *  Still a prop rather than hard-coded, because this row is rendered by
     *  more than one list and a future one may have the time in its own
     *  header; every caller today passes it. */
    showModified?: boolean;
    /** Tell this row's load on the row: percent, stage, error, Cancel and Dismiss. While
     *  set, the row claims its load (`claimLoad`), so the progress toast leaves it out. */
    loadProgress?: RowLoadProgress;
}

const FileRow: React.FC<FileRowProps> = ({
    file: f,
    displayName,
    indentLevel,
    viewingName,
    loadedSourceNames,
    conversionJobs,
    expandedName,
    setExpandedName,
    onToggle,
    setPickerName,
    isSelected,
    isQueued,
    onSelectToggle,
    menuItems,
    onOpenContextMenu,
    rowKey,
    focused,
    draggable,
    onDragStartRow,
    onDragEndRow,
    onDropAt,
    dimmed,
    renaming,
    onRenameCommit,
    onRenameCancel,
    showModified,
    loadProgress: lp,
}) => {
    const isViewing = viewingName === f.name;
    const otherViewing = viewingName !== null && !isViewing;
    const isLoaded = loadedSourceNames.has(f.name);
    const viewJob = isViewing ? conversionJobs[`${f.name}::glb`] : undefined;
    const viewProgressPct = viewJob
        ? Math.max(0, Math.min(100, Math.round(viewJob.progress * 100)))
        : 0;
    const indentPx = indentLevel * 12;
    const queued = lp ? !!isQueued || lp.state.phase === "queued" : isQueued;
    const loadPct = lp && isViewing ? lp.state.percent : null;
    const loadEnded = !!lp && !isViewing && (lp.state.phase === "error" || lp.state.phase === "cancelled");
    const hasProgress = !!lp;
    useEffect(() => (hasProgress ? claimLoad(f.name) : undefined), [f.name, hasProgress]);

    // Long-press = the touch path to the context menu (desktop has
    // right-click). 500 ms hold, cancelled by pointer move > 8 px
    // (treats it as a scroll, not a hold) or by a drag starting.
    const longPressTimer = useRef<number | null>(null);
    const longPressStart = useRef<{x: number; y: number} | null>(null);
    const longPressFired = useRef(false);
    const cancelLongPress = () => {
        if (longPressTimer.current !== null) {
            window.clearTimeout(longPressTimer.current);
            longPressTimer.current = null;
        }
    };
    const onPointerDown: React.PointerEventHandler = (e) => {
        // Touch only — on mouse/pen the menu lives on right-click, and a
        // held left button must stay free to start an HTML5 drag (which
        // begins on movement; a timer firing mid-hold stole the gesture).
        if (e.pointerType !== "touch") return;
        const {clientX, clientY} = e;
        longPressStart.current = {x: clientX, y: clientY};
        longPressFired.current = false;
        cancelLongPress();
        longPressTimer.current = window.setTimeout(() => {
            longPressFired.current = true;
            onOpenContextMenu?.({clientX, clientY});
        }, 500);
    };
    const onPointerMove: React.PointerEventHandler = (e) => {
        if (!longPressStart.current) return;
        const dx = e.clientX - longPressStart.current.x;
        const dy = e.clientY - longPressStart.current.y;
        if (dx * dx + dy * dy > 64) cancelLongPress();
    };
    const onPointerUp: React.PointerEventHandler = () => {
        cancelLongPress();
        longPressStart.current = null;
    };
    useEffect(() => () => cancelLongPress(), []);

    return (
        <li
            data-rowkey={rowKey}
            className={
                "flex flex-col pr-1 py-1 text-xs rounded cursor-pointer select-none " +
                (dimmed ? "opacity-40 " : "") +
                (focused ? "ring-1 ring-blue-400/70 " : "") +
                (isSelected ? "bg-amber-700/30 " : "hover:bg-gray-800/60 ")
            }
            style={{paddingLeft: `${8 + indentPx}px`}}
            draggable={draggable || undefined}
            onDragStart={draggable && onDragStartRow ? (e) => {
                cancelLongPress();
                onDragStartRow(e);
            } : undefined}
            onDragEnd={onDragEndRow}
            onDragOver={onDropAt ? (e) => e.preventDefault() : undefined}
            onDrop={onDropAt ? (e) => {
                e.preventDefault();
                e.stopPropagation();
                onDropAt(e);
            } : undefined}
            onContextMenu={onOpenContextMenu}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={() => {
                cancelLongPress();
                longPressStart.current = null;
            }}
            onClick={(e) => {
                if (longPressFired.current) {
                    // The long-press already opened the context menu;
                    // the synthetic click fires after pointerup and
                    // would also toggle selection if let through.
                    longPressFired.current = false;
                    e.stopPropagation();
                    return;
                }
                // Single click/tap = selection toggle (feeds the bulk
                // toolbar); shift-click selects the visible range from
                // the last toggled row. Context menu is right-click /
                // long-press.
                onSelectToggle(f.name, e.shiftKey);
            }}
        >
            <div className="flex items-center justify-between gap-2">
                {/* The checkbox IS the load toggle — checked (+ the
                    eye marker) while the model is in the scene; clicking
                    it loads/unloads directly. Row click = selection
                    (amber highlight feeds the bulk toolbar). */}
                {(
                    <input
                        type="checkbox"
                        className="h-5 w-5 shrink-0 cursor-pointer disabled:cursor-not-allowed"
                        checked={isLoaded || queued || isViewing}
                        onChange={() => void onToggle(f, !(isLoaded || queued))}
                        onClick={(e) => e.stopPropagation()}
                        // One predicate for all three routes into the scene —
                        // the streaming bake, the convert pipeline, and a
                        // plugin-registered renderable-file provider. This was
                        // `!isStreamingFEAResult && !canLoadIntoSceneLegacy`
                        // here and in four other places, i.e. one place per
                        // opportunity to forget the third route.
                        disabled={isViewing || !canOpenInScene(f.name)}
                        aria-busy={isViewing || undefined}
                        // A disabled checkbox with a "Load into scene" tooltip
                        // reads as broken. Say why it is off instead.
                        title={!canOpenInScene(f.name)
                            ? "Nothing installed can open this file type"
                            : isLoaded
                                ? "Unload from scene"
                                : queued
                                    ? "Queued to load — untick to remove from the queue"
                                    : isStreamingFEAResult(f.name)
                                        ? "Open in streaming FEA viewer (queues if another model is loading)"
                                        : "Load into scene (queues if another model is loading)"}
                    />
                )}
                <FileTypeIcon name={f.name}/>
                {renaming && onRenameCommit && onRenameCancel ? (
                    <InlineNameInput
                        initial={displayName}
                        selectStem
                        onCommit={onRenameCommit}
                        onCancel={onRenameCancel}
                    />
                ) : (
                    <button
                        type="button"
                        onClick={(e) => {
                            e.stopPropagation();
                            onSelectToggle(f.name, e.shiftKey);
                        }}
                        className={`flex-1 min-w-0 text-left ${expandedName === f.name ? 'whitespace-normal break-all' : 'truncate'} ${isLoaded ? 'text-blue-200 font-medium' : ''}`}
                        title={f.name}
                    >
                        {displayName}
                    </button>
                )}
                <div className="flex items-center gap-1 shrink-0">
                    {/* `hidden sm:inline`: the relative time is worth 60px of a
                        448px desktop panel and is not worth it on a phone,
                        where the same 60px is a fifth of the row. Rendered
                        nothing at all when the entry carries no timestamp --
                        an empty span still takes its gap and reads as a value
                        that failed to load.

                        The tooltip is the ABSOLUTE time, in the viewer's own
                        timezone. "2 d ago" answers which of two files is newer;
                        it does not answer whether this is the copy you uploaded
                        at 10:49, and that is the next question every time. */}
                    {showModified && parseLastModifiedMs(f.lastModified) > 0 && (
                        <span
                            className="hidden sm:inline text-[10px] text-gray-400 tabular-nums whitespace-nowrap"
                            title={localDateTime(f.lastModified)}
                        >
                            {formatRelative(f.lastModified)}
                        </span>
                    )}
                    {/* Explicit "in scene" marker. The checkbox is a
                        selection control (bulk actions), so loaded
                        state needs its own signal — the blue filename
                        tint alone is easy to miss on mobile. */}
                    {queued && (
                        <span className="text-[10px] text-amber-400 uppercase tracking-wide shrink-0">
                            queued
                        </span>
                    )}
                    {isLoaded && !isViewing && (
                        <ViewIcon
                            width="16px"
                            height="16px"
                            className="text-blue-400"
                            aria-label="Loaded in scene"
                        />
                    )}
                    {isViewing && loadPct === null && <Spinner/>}
                    {/* Legacy single-shot (step, field) picker — kept
                        only for hypothetical future non-streaming FEA
                        formats. SIF goes through the streaming bake
                        now (toggle the checkbox; refine field /
                        reduction / step in SimulationControls), so
                        the picker entry point would just confuse the
                        user with two parallel ways to load the same
                        file. Gated on ``!isStreamingFEAResult`` so
                        the moment a new isFEAResult format that is
                        NOT in the streaming set ships, the picker
                        re-appears for it without code changes here. */}
                    {isFEAResult(f.name) && !isStreamingFEAResult(f.name) && runtime.isRestMode() && runtime.convertEnabled() && (
                        <button
                            className="p-1 rounded-sm text-white hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
                            onClick={(e) => {
                                e.stopPropagation();
                                setPickerName(f.name);
                            }}
                            disabled={otherViewing || isViewing}
                            title="Pick step / field"
                            aria-label="Pick step / field"
                        >
                            <span className="leading-none text-sm font-mono">⇅</span>
                        </button>
                    )}
                    {menuItems.length > 0 && (
                        <span onClick={(e) => e.stopPropagation()}>
                            <RowKebabMenu
                                ariaLabel={`Actions for ${displayName}`}
                                buttonClassName="h-7 w-7 text-gray-200 hover:bg-gray-700"
                                header={<span className="font-mono" title={f.name}>{f.name}</span>}
                                items={menuItems}
                            />
                        </span>
                    )}
                </div>
            </div>
            {isViewing && lp && (
                <div className="mt-1 flex flex-col gap-0.5">
                    <div
                        className="h-1 w-full bg-gray-700 rounded-sm overflow-hidden"
                        role="progressbar"
                        aria-label={`Loading ${displayName}`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={loadPct ?? undefined}
                        aria-valuetext={lp.state.stage || undefined}
                    >
                        {loadPct !== null ? (
                            <div className="h-full bg-blue-600 transition-[width] duration-200" style={{width: `${loadPct}%`}}/>
                        ) : (
                            <div className="h-full w-1/3 bg-blue-600 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                        )}
                    </div>
                    <div className="flex items-center gap-2 min-w-0 text-[10px] leading-tight text-gray-400">
                        <span className="truncate flex-1 min-w-0" title={lp.state.stage || undefined}>
                            {lp.state.stage || "Loading…"}
                        </span>
                        {loadPct !== null && <span className="shrink-0 tabular-nums">{loadPct}%</span>}
                        {lp.state.cancel && (
                            <button
                                type="button"
                                className="shrink-0 rounded-sm px-1 hover:text-red-300 hover:bg-gray-700"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    lp.onCancel();
                                }}
                                title="Stop this load, and the conversion running for it"
                                aria-label={`Cancel loading ${displayName}`}
                            >
                                Cancel
                            </button>
                        )}
                    </div>
                </div>
            )}
            {loadEnded && lp && (
                <div className="mt-0.5 flex items-center gap-1 min-w-0 text-[10px] leading-tight" role="status">
                    <span
                        className={"truncate flex-1 min-w-0 " + (lp.state.phase === "error" ? "text-red-400" : "text-gray-400")}
                        title={lp.state.message ?? undefined}
                    >
                        {lp.state.phase === "error" ? `Load failed: ${lp.state.message}` : lp.state.message}
                    </span>
                    <button
                        type="button"
                        className="shrink-0 rounded-sm px-1 text-gray-400 hover:text-white hover:bg-gray-700"
                        onClick={(e) => {
                            e.stopPropagation();
                            lp.onDismiss();
                        }}
                        title="Dismiss"
                        aria-label={`Dismiss load status for ${displayName}`}
                    >
                        ×
                    </button>
                </div>
            )}
            {isViewing && !lp && (
                <div className="mt-1 h-1 w-full bg-gray-700 rounded-sm overflow-hidden">
                    {viewJob && viewJob.status !== 'queued' ? (
                        <div
                            className="h-full bg-blue-600 transition-[width] duration-200"
                            style={{width: `${viewProgressPct}%`}}
                        />
                    ) : (
                        <div className="h-full w-1/3 bg-blue-600 animate-[indeterminate_1.4s_ease-in-out_infinite]"/>
                    )}
                </div>
            )}
        </li>
    );
};

export default FileRow;
