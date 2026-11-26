// The Selected Object panel's "selection-summary" section: the name row, the multi-select copy
// pill, and the visibility / selection actions (Hide, Hide others, Unhide all, Fit all, Show in
// data, Download as…, Add mode) -- or, with `summary: "menu"`, the same actions folded into one
// small "⋯" menu instead. Split out of ObjectInfoBoxComponent.tsx: by far the largest of its
// sections (it alone was most of that file's length), and self-contained once it has the options
// context and the two selection hooks.

import React, {useState} from "react";
import type {SelectionSnapshot} from "@/plugins/propertyProviders";
import {useViewerStores} from "@/state/AdaViewerContext";
import {useCellBuilderStore} from "@/state/cellBuilderStore";
import {useSceneMirrorStore} from "@/state/sceneMirrorStore";
import {parentLevelName} from "@/utils/tree_view/treeSelection";
import {isMirroredWindow, useSceneActions} from "@/layout/sceneActions";
import {copySelectionNames, writeToClipboard} from "@/utils/clipboard/copySelectionNames";
import {elementFirstNodeId} from "@/utils/scene/fea/goToNode";
import {useClickedPosition} from "../ObjectMetadataPanel";
import {meshStatsLine, useSelectionMeshStats} from "../MeshStatsSection";
import DownloadSelectionButton from "../DownloadSelectionButton";
import {ObjectInfoOptions} from "../objectInfoOptions";
import {useSelectedRangeCount} from "../useSelectionSnapshot";

// 1500 ms is the smallest hold that still feels intentional vs a
// reflexive tap-and-release; long enough that "Copied" lingers on
// screen for a beat after the haptic-style feedback most users
// expect from a copy gesture.
const COPIED_FEEDBACK_MS = 1500;

/**
 * The summary as one small "⋯" menu at the top right of the body (`summary: "menu"`): Show in data,
 * Download as…, Copy clicked position, the mesh's stats. It takes no row: the items below name the
 * pick themselves.
 */
const SummaryMenu: React.FC<{onShowInData: (() => void) | null; cellCtx: boolean}> = ({onShowInData, cellCtx}) => {
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState(false);
    const position = useClickedPosition();
    const mesh = useSelectionMeshStats();
    const item = "block w-full text-left text-[11px] text-white px-2 py-1 rounded-sm hover:bg-gray-600 disabled:opacity-40 disabled:hover:bg-transparent";
    return (
        <div className="relative h-0" data-summary-menu="">
            <button
                type="button"
                className="absolute right-0 -top-0.5 z-10 rounded-sm px-1.5 leading-5 text-gray-300 hover:bg-white/10 hover:text-white"
                aria-label="Selection actions"
                aria-haspopup="menu"
                aria-expanded={open}
                title="Selection actions"
                onClick={() => setOpen((v) => !v)}
            >
                ⋯
            </button>
            {open && (
                <div role="menu" className="absolute right-0 top-5 z-20 min-w-48 rounded-sm border border-gray-600 bg-gray-800 p-1 shadow-lg" data-summary-menu-items="">
                    {onShowInData && (
                        <button type="button" role="menuitem" className={item} onClick={() => (onShowInData(), setOpen(false))}>
                            Show in data
                        </button>
                    )}
                    {/* A cell is not a tree row; its own model has its own export. */}
                    {!cellCtx && <DownloadSelectionButton inMenu/>}
                    <button
                        type="button"
                        role="menuitem"
                        className={item}
                        disabled={!position}
                        title={position ? `Copy ${position.display} as comma-separated floats` : "Click on the model to get a position"}
                        onClick={() => {
                            if (!position) return;
                            void writeToClipboard(position.copy).then((ok) => {
                                if (!ok) return;
                                setCopied(true);
                                window.setTimeout(() => setCopied(false), COPIED_FEEDBACK_MS);
                            });
                        }}
                    >
                        {copied ? "Copied ✓" : position ? `Copy clicked position ${position.display}` : "Copy clicked position"}
                    </button>
                    {mesh && (
                        <div role="menuitem" aria-disabled="true" className="px-2 py-1 text-[11px] text-gray-400" title="The selection's tessellation">
                            Mesh: {meshStatsLine(mesh)}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export const SelectionSummary = ({sel}: {sel: SelectionSnapshot}) => {
    const {onShowInData: showInData, summary: summaryMode} = React.useContext(ObjectInfoOptions);
    // What the buttons do: on this window's scene, or -- in a pop-out window --
    // sent to the main window, which has it (`@/layout/sceneActions`).
    const actions = useSceneActions();
    const mirrored = isMirroredWindow();
    const mirroredParent = useSceneMirrorStore((s) => s.parentName);
    const {
        useSelectedObjectStore,
        useTableNavStore,
        useFeaAnimationStore,
        useTreeViewStore,
    } = useViewerStores();
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    const additiveMode = useSelectedObjectStore((s) => s.additiveMode);
    const toggleAdditiveMode = useSelectedObjectStore((s) => s.toggleAdditiveMode);

    // Procedural cell context: when a cellbuilder model has a selection, this
    // one panel represents the selected cell(s) — name, copy, visibility and
    // add-mode all target cells rather than regular draw ranges. A click that
    // misses every cell still routes through the regular path below.
    const cbActive = useCellBuilderStore((s) => s.active !== null);
    const cbSelection = useCellBuilderStore((s) => s.selection);
    const cbSelectedIds = useCellBuilderStore((s) => s.selectedCellIds);
    const cbSelectedRefIds = useCellBuilderStore((s) => s.selectedRefIds);
    const cbCells = useCellBuilderStore((s) => s.cells);
    const cbRefs = useCellBuilderStore((s) => s.refs);
    const cbAddMode = useCellBuilderStore((s) => s.cellAddMode);
    const hideCells = useCellBuilderStore((s) => s.hideCells);
    const toggleCellAddMode = useCellBuilderStore((s) => s.toggleCellAddMode);
    // Follows the snapshot's kind, not the raw stores: with `selectionSource="scene"` (Output
    // etc.) a builder pick that lingers from another mode must not make this panel show as a
    // cell/ref context, and the reverse. useSelectionSnapshot already folds in whether a builder
    // pick is this panel's to show at all (useBuilderPickShown) when it computed `sel.kind`.
    const cellCtx = sel.kind === "cell" || sel.kind === "ref";
    // A mixed multi-select copies both kinds' names together.
    const cellNames = [
        ...cbSelectedIds.map((id) => cbCells[id]?.name),
        ...cbSelectedRefIds.map((id) => cbRefs[id]?.name),
    ].filter((n): n is string => !!n);

    // Whether the scene has anything at all — a loaded model (tree) or builder
    // cells. Scene-wide recovery actions (Unhide all) stay available as long as
    // entities remain, even with nothing selected, so hiding the last selection
    // doesn't strand what you hid.
    const treeData = useTreeViewStore((s) => s.treeData);
    const hasEntities = treeData != null || (cbActive && Object.keys(cbCells).length > 0);

    // Tree-level "up": the parent level the current selection would climb to.
    // null hides the arrow (nothing selected, already at a file root, or a
    // cellbuilder selection — cells aren't tree nodes). Recomputed when the
    // selection or the tree changes. Shown on mobile and desktop; desktop also
    // has the Shift+ArrowUp shortcut.
    // In a pop-out window: the main window's (it holds the 3D selection).
    const localParent = React.useMemo(
        () => (cellCtx || mirrored ? null : parentLevelName()),
        [selectedObjects, treeData, cellCtx, mirrored],
    );
    const parentName = mirrored ? mirroredParent : localParent;
    const onSelectParent = actions.selectParent;

    // Total drawRangeIds across all selected meshes — that's the
    // count of "things selected" the user thinks of (one per
    // clicked element, regardless of how many meshes back them).
    const rangeCount = useSelectedRangeCount();
    // Effective selection identity — the snapshot's own `name` already picks the cell's under a
    // cell/ref context and the picked element's otherwise (and, per selectionSource, "" rather
    // than a stale pick left over from another mode: see the `cellCtx` comment above).
    const displayName = sel.name ?? '';
    const multiSelectCount = cellCtx ? cbSelectedIds.length + cbSelectedRefIds.length : rangeCount;
    const isMultiSelect = multiSelectCount > 1;
    const addModeOn = cellCtx ? cbAddMode : additiveMode;

    // Visibility / selection actions dispatch to the active source. Unhide-all
    // always clears both (regular ranges + cell hides) so "show everything"
    // never leaves something hidden in the other system (`actions.showAll`).
    // A mixed multi-select hides both sets together; with neither set populated (a lone pick),
    // fall back to the primary -- a cell/face/edge's cellId, or a ref's refId -- to hide it too.
    const onHide = () => {
        if (!cellCtx) {
            actions.hideSelection();
            return;
        }
        const ids = cbSelectedIds.length || cbSelectedRefIds.length
            ? [...cbSelectedIds, ...cbSelectedRefIds]
            : cbSelection?.cellId
              ? [cbSelection.cellId]
              : cbSelection?.kind === "ref"
                ? [cbSelection.refId]
                : [];
        if (ids.length) hideCells(ids);
    };
    const onUnhideAll = actions.showAll;
    const onToggleAddMode = () => (cellCtx ? toggleCellAddMode() : toggleAdditiveMode());

    const [copied, setCopied] = useState<"single" | "multi" | null>(null);
    const flashCopied = (which: "single" | "multi") => {
        setCopied(which);
        window.setTimeout(() => setCopied((c) => (c === which ? null : c)), COPIED_FEEDBACK_MS);
    };

    const onCopySingle = async () => {
        if (!displayName) return;
        const ok = await writeToClipboard(displayName);
        if (ok) flashCopied("single");
    };
    const onCopyAll = async () => {
        if (cellCtx) {
            if (!cellNames.length) return;
            const ok = await writeToClipboard(cellNames.join("\n"));
            if (ok) flashCopied("multi");
            return;
        }
        const n = await copySelectionNames();
        if (n > 0) flashCopied("multi");
    };

    // "Show in data" — visible only when the picked element resolves
    // to a vertex on the active FEA mesh (so the button doesn't
    // appear on unrelated CAD picks). Click opens the data table
    // panel and scrolls to the element's first node.
    const feaSessionActive = useFeaAnimationStore((s) => s.sessionActive);
    const setPanelOpen = useTableNavStore((s) => s.setPanelOpen);
    const setGoToTarget = useTableNavStore((s) => s.setGoToTarget);
    // Gated on the snapshot's own kind === "mesh", not the raw store or `meshName` (which is not
    // itself selectionSource-aware): with a cell/ref context, or with selectionSource having
    // suppressed a stale scene pick, there is no mesh element to show in the data table, whatever
    // a lingering raw pick from another mode might still say.
    const firstNodeId = sel.kind === "mesh" && sel.meshName && feaSessionActive ? elementFirstNodeId(sel.meshName) : null;
    const onShowInData = () => {
        if (firstNodeId == null) return;
        if (showInData) return showInData(firstNodeId);
        setPanelOpen(true);
        setGoToTarget({kind: "node", id: firstNodeId});
    };

    // Camera actions — desktop has keyboard shortcuts (zoom-fit / center-on-selection), but
    // mobile has no keyboard, so surface them as buttons in this panel. They are Shift+A and
    // Shift+F's own actions, which frame builder cells (excluded from the fittable scene) too.
    const onFitAll = actions.fitAll;
    const onGoToObject = actions.fitSelection;

    if (summaryMode === "menu") {
        return displayName ? <SummaryMenu onShowInData={firstNodeId != null ? onShowInData : null} cellCtx={cellCtx}/> : null;
    }

    return (
        <>
            {/* Name. Two layouts because mobile and desktop have very
                different real-estate constraints:

                * Mobile (< 640 px): the "Name:" label is implicit
                  — the panel header already says "Selected Object
                  Info" and the very next line being the name reads
                  fine without it. The name button gets the full
                  panel width so long element names have room to
                  wrap without truncating off-screen.
                * Desktop (≥ 640 px): keep the ``Name:`` label in a
                  fixed-width column for symmetry with the other
                  rows. Nothing wins from dropping it — the screen
                  has the space.
                On both, the name acts as a tap-to-copy target on
                mobile (iOS/Android long-press idiom) and falls back
                to plain selectable text on desktop where Shift+C
                covers the multi-name copy. */}
            {displayName && (
                <>
                    <div className="hidden sm:table-row">
                        <div className="table-cell w-24">Name:</div>
                        <div className="table-cell w-48 break-all">
                            <div className="flex items-start gap-1">
                                {parentName && (
                                    <ParentUpButton parentName={parentName} onUp={onSelectParent}/>
                                )}
                                <div className="break-all flex-1">
                                    <NameCopyButton
                                        name={displayName}
                                        copied={copied === "single"}
                                        onCopy={onCopySingle}
                                    />
                                </div>
                            </div>
                        </div>
                    </div>
                    <div className="sm:hidden flex items-start gap-1">
                        {parentName && (
                            <ParentUpButton parentName={parentName} onUp={onSelectParent}/>
                        )}
                        <div className="break-all flex-1">
                            <NameCopyButton
                                name={displayName}
                                copied={copied === "single"}
                                onCopy={onCopySingle}
                            />
                        </div>
                    </div>
                </>
            )}
            {/* Multi-select copy pill. Regular selections use Shift+C on desktop
                so it's mobile-only there; procedural cells have no such shortcut,
                so show it on all sizes when several cells are selected. Only
                renders when > 1 thing is selected. */}
            {displayName && isMultiSelect && (
                <div className={(cellCtx ? "" : "sm:hidden ") + "mt-1"}>
                    <button
                        type="button"
                        onClick={() => void onCopyAll()}
                        className="bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white text-[11px] rounded-full px-2 py-0.5"
                        aria-label={`Copy all ${multiSelectCount} selected names to clipboard`}
                    >
                        {copied === "multi"
                            ? `Copied ${multiSelectCount} ✓`
                            : `Copy all (${multiSelectCount})`}
                    </button>
                </div>
            )}
            {/* Visibility actions. Hide is the canonical "make this
                disappear" operation for already-loaded geometry —
                distinct from the storage browser's "Clear" which
                unloads the file entirely. The two live in
                different panels because they act on different
                things (selection vs scope). Visible on mobile and
                desktop both: Shift+H/U exists but is undiscoverable;
                buttons make the operation findable without
                cluttering desktop unnecessarily (single row, small
                pills). */}
            {(displayName || hasEntities) && (
                <div className="mt-2 flex flex-wrap gap-2 items-center">
                    {/* Hide acts on the current selection, so it only shows when
                        something is selected. */}
                    {displayName && (
                        <button
                            type="button"
                            onClick={onHide}
                            className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded-sm px-2 py-1 inline-flex items-center gap-1"
                            title={
                                cellCtx
                                    // A mixed multi-select may be cells and refs together, so the
                                    // plural drops "cells"; a lone pick still names what it actually is.
                                    ? (isMultiSelect
                                        ? `Hide ${multiSelectCount} selected`
                                        : cbSelection?.kind === "ref" ? "Hide selected ref" : "Hide selected cell")
                                    : isMultiSelect
                                        ? `Hide ${multiSelectCount} selected (Shift+H)`
                                        : "Hide selected (Shift+H)"
                            }
                            aria-label="Hide selected"
                        >
                            <EyeOffIcon/>
                            Hide
                            {isMultiSelect ? ` (${multiSelectCount})` : ""}
                        </button>
                    )}
                    {/* The inverse of Hide: keep the selection, hide the rest. Not for builder
                        cells, which have their own visibility system. */}
                    {displayName && !cellCtx && (
                        <button
                            type="button"
                            onClick={actions.hideOthers}
                            className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded-sm px-2 py-1 inline-flex items-center gap-1"
                            title="Hide everything that is not selected (Shift+I)"
                            aria-label="Hide unselected"
                        >
                            <EyeOffIcon/>
                            Hide others
                        </button>
                    )}
                    {/* Unhide all is a scene-wide recovery action — it stays
                        available as long as any entity remains, even with no
                        selection, so you can always undo a Hide. */}
                    <button
                        type="button"
                        onClick={onUnhideAll}
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded-sm px-2 py-1 inline-flex items-center gap-1"
                        title="Unhide everything hidden in the scene (cells and geometry) (Shift+U)"
                        aria-label="Unhide all"
                    >
                        <EyeIcon/>
                        Unhide all
                    </button>
                    {/* Fit all frames the whole scene — a scene-wide action like
                        Unhide all, so it stays next to it and available even with
                        nothing selected. Mobile-only (desktop has Shift+A). */}
                    <button
                        type="button"
                        onClick={onFitAll}
                        className="sm:hidden bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded-sm px-2 py-1 inline-flex items-center gap-1"
                        title="Fit the whole model to the view"
                        aria-label="Fit all to view"
                    >
                        Fit all
                    </button>
                    {/* The rest act on the current selection — hidden when
                        nothing is selected (only Unhide all persists). */}
                    {displayName && (<>
                    {firstNodeId != null && (
                        <button
                            type="button"
                            onClick={onShowInData}
                            className="bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white text-[11px] rounded-sm px-2 py-1 inline-flex items-center gap-1"
                            title={`Open the FEA data table and scroll to node ${firstNodeId} (this element's first node)`}
                            aria-label="Show this element in the FEA data table"
                        >
                            <TableIcon/>
                            Show in data
                        </button>
                    )}
                    {/* The selection (and everything under it) as a STEP/IFC file. Not for
                        procedural cells: a cell is not a tree row, and its own model has its
                        own export in the cellbuilder. */}
                    {!cellCtx && <DownloadSelectionButton/>}
                    {/* Camera button — mobile-only (desktop has keyboard
                        shortcuts). "Go to object" frames the current selection;
                        "Fit all" (scene-wide) lives next to Unhide all above. */}
                    <button
                        type="button"
                        onClick={onGoToObject}
                        className="sm:hidden bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded-sm px-2 py-1 inline-flex items-center gap-1"
                        title="Center the view on the selected object"
                        aria-label="Center view on selected object"
                    >
                        Go to object
                    </button>
                    {/* Additive selection toggle. Sticky — stays on across
                        clicks (and deselects) so a multi-pick session is
                        uninterrupted; click again to turn off. For regular
                        geometry it's mobile-only (desktop has Shift+click); for
                        procedural cells there's no such shortcut, so show it on
                        all sizes. */}
                    <button
                        type="button"
                        onClick={onToggleAddMode}
                        className={
                            (cellCtx ? "" : "sm:hidden ") +
                            "text-[11px] rounded-sm px-2 py-1 text-white " +
                            (addModeOn
                                ? "bg-amber-600 hover:bg-amber-500 active:bg-amber-700"
                                : "bg-gray-700 hover:bg-gray-600 active:bg-gray-800")
                        }
                        title={
                            addModeOn
                                ? "Clicking adds to the selection. Click to switch back to single-select."
                                : "Switch to multi-select: subsequent clicks add to the selection instead of replacing it."
                        }
                        aria-pressed={addModeOn}
                        aria-label={addModeOn ? "Disable add-to-selection" : "Enable add-to-selection"}
                    >
                        {addModeOn ? "✓ Add mode on" : "+ Add mode"}
                    </button>
                    </>)}
                </div>
            )}
        </>
    );
};

// Tap-to-copy name button. Used twice — once inside the desktop
// table-row layout, once standalone on mobile — so the styling +
// copied-feedback wiring lives in one place. On mobile the press
// state is interactive (visible hover/active highlight); on desktop
// it falls back to plain selectable text (cursor-text + select-text)
// so users can still click into the cell to highlight + Cmd-C copy.
const NameCopyButton: React.FC<{
    name: string;
    copied: boolean;
    onCopy: () => Promise<void>;
}> = ({name, copied, onCopy}) => (
    <button
        type="button"
        onClick={() => void onCopy()}
        className={
            "text-left break-all w-full font-medium " +
            "sm:cursor-text sm:select-text sm:font-normal sm:bg-transparent sm:hover:bg-transparent " +
            "rounded-sm px-1 py-0.5 -mx-1 -my-0.5 hover:bg-blue-700/40 active:bg-blue-700/60"
        }
        aria-label="Copy name to clipboard"
        title="Tap to copy"
    >
        {copied ? `${name} ✓` : name}
    </button>
);

// Up-a-level button next to the selected name. Selects the parent tree level of
// the current selection; the tooltip names that parent (hover shows it on
// desktop). Shown on both mobile and desktop; desktop also has the Shift+ArrowUp
// shortcut (see setupCameraControlsHandlers). Does not open the tree panel.
// Rendered only when a parent level exists.
const ParentUpButton: React.FC<{parentName: string; onUp: () => void}> = ({parentName, onUp}) => (
    <button
        type="button"
        onClick={onUp}
        className="shrink-0 mt-0.5 bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white rounded-sm p-1 inline-flex items-center"
        title={`Select parent level: ${parentName}`}
        aria-label={`Select parent level: ${parentName}`}
    >
        <ChevronUpIcon/>
    </button>
);

const ChevronUpIcon: React.FC = () => (
    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
        <path d="M3.5 10 8 5.5 12.5 10"/>
    </svg>
);

// Inline SVG icons. Inherit ``currentColor`` so they pick up the
// button text colour rather than rendering platform-specific emoji
// colours (the previous 🚫 rendered red on every OS, breaking the
// neutral grey button palette).
const EyeIcon: React.FC = () => (
    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
        <path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/>
        <circle cx="8" cy="8" r="2"/>
    </svg>
);

const EyeOffIcon: React.FC = () => (
    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
        <path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"/>
        <circle cx="8" cy="8" r="2"/>
        <path d="M2 14 14 2"/>
    </svg>
);

const TableIcon: React.FC = () => (
    <svg viewBox="0 0 16 16" className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden>
        <rect x="2" y="3" width="12" height="10" rx="1"/>
        <path d="M2 7h12M6 3v10"/>
    </svg>
);
