import React, {useEffect, useRef} from "react";
import {ServerFileEntry} from "@/state/serverInfoStore";
import {runtime} from "@/runtime/config";
import FileTypeIcon from "../icons/FileTypeIcon";
import ViewIcon from "../icons/ViewIcon";
import {RowKebabMenu} from "@/components/common/RowKebabMenu";
import InlineNameInput from "@/components/common/InlineNameInput";
import {KebabMenuItem} from "@/components/common/PositionedMenu";
import {canLoadIntoSceneLegacy, isFEAResult, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {formatRelative} from "./storageHelpers";
import {Spinner} from "./Spinner";


// Custom drag MIME for in-panel file moves. OS-file drops arrive as
// ``dataTransfer.files`` instead; checking for this type tells the two
// apart (types are readable during dragover, the payload only on drop).
// One file row: name, size, age, load toggle, kebab menu, conversion state. Moved
// verbatim out of StorageBrowser — the largest single presentational piece in it.
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
    /** Maximized view: show the last-modified column. */
    showModified?: boolean;
}

export const FileRow: React.FC<FileRowProps> = ({
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
}) => {
    const isViewing = viewingName === f.name;
    const otherViewing = viewingName !== null && !isViewing;
    const isLoaded = loadedSourceNames.has(f.name);
    const viewJob = isViewing ? conversionJobs[`${f.name}::glb`] : undefined;
    const viewProgressPct = viewJob
        ? Math.max(0, Math.min(100, Math.round(viewJob.progress * 100)))
        : 0;
    const indentPx = indentLevel * 12;

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
                        checked={isLoaded || isQueued || isViewing}
                        onChange={() => void onToggle(f, !(isLoaded || isQueued))}
                        onClick={(e) => e.stopPropagation()}
                        disabled={
                            isViewing ||
                            (!isStreamingFEAResult(f.name) && !canLoadIntoSceneLegacy(f.name))
                        }
                        aria-busy={isViewing || undefined}
                        title={isLoaded
                            ? "Unload from scene"
                            : isQueued
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
                    {showModified && (
                        <span
                            className="text-[10px] text-gray-400 tabular-nums whitespace-nowrap"
                            title={f.lastModified}
                        >
                            {formatRelative(f.lastModified)}
                        </span>
                    )}
                    {/* Explicit "in scene" marker. The checkbox is a
                        selection control (bulk actions), so loaded
                        state needs its own signal — the blue filename
                        tint alone is easy to miss on mobile. */}
                    {isQueued && (
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
                    {isViewing && <Spinner/>}
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
            {isViewing && (
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

// ──────────────────────────────────────────────────────────────────
// VersionsTree: renders the CI-uploaded ``versions/<branch>/<sha>/…``
// blobs as a 3-level collapsible list:
//
//   Versions
//   ├ <branch>                   ← collapsible. Sorted newest-tip first.
//   │  ├ <commit (relative t)>   ← Latest of branch is auto-expanded
//   │  │  ├ welds_model.glb      ← <FileRow indentLevel=2/>
//   │  │  └ welds_model.ifc
//   │  └ <older commit>          ← collapsed by default
//   └ <other branch>
//
// All branches collapse-by-default except the most recently active
// one, whose latest commit is also auto-expanded. State is local to
// the panel; refresh resets it.
//
// Version blobs are CI build outputs — read-only by design. Their
// rows get load/download menus only: no rename/move/delete, no drag,
// no drop targets.
// ──────────────────────────────────────────────────────────────────

