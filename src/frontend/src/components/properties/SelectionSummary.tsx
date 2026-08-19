// Selection identity and actions — name, copy, hide/unhide, fit, show-in-data,
// add-mode. Extracted VERBATIM from ObjectInfoBoxComponent: only the outer panel
// chrome was removed and the two child panels were promoted to sibling providers.
// The cell-vs-mesh dispatch below is subtle and correct; it was moved, not rewritten.
import React, {useState} from 'react';
import {useViewerStores} from '@/state/AdaViewerContext';
import {copySelectionNames, writeToClipboard} from '@/utils/clipboard/copySelectionNames';
import {hideSelectedRanges, unhideAllRanges} from '@/utils/scene/visibility';
import {elementFirstNodeId} from '@/utils/scene/fea/goToNode';
import {centerViewOnSelection} from '@/utils/scene/centerViewOnSelection';
import {frameCells} from '@/utils/scene/frameCells';
import {zoomToAll} from '@/components/viewer/sceneHelpers/setupCameraControlsHandlers';
import {sceneRef, cameraRef, controlsRef} from '@/state/refs';
import {requestRender} from '@/state/perfStore';
import {useCellBuilderStore} from '@/state/cellBuilderStore';
import {parentLevelName, selectParentLevel} from '@/utils/tree_view/treeNavigation';

// 1500 ms is the smallest hold that still feels intentional vs a
// reflexive tap-and-release; long enough that "Copied" lingers on
// screen for a beat after the haptic-style feedback most users
// expect from a copy gesture.
const COPIED_FEEDBACK_MS = 1500;

// Shared button recipes. These panels sit in a dense properties list, so they use the
// design system's smallest control size and semantic surfaces rather than the palette
// colours this file carried before the split.
const ACTION_BASE =
    "ada-focus inline-flex items-center gap-1 shrink-0 px-2 h-control-sm min-h-control-sm " +
    "rounded-sm text-xs font-medium whitespace-nowrap transition-colors duration-(--ada-dur-fast) " +
    "disabled:opacity-50 disabled:pointer-events-none";
const SECONDARY_ACTION =
    ACTION_BASE + " bg-surface-2 text-content border border-edge pointer-fine:hover:bg-surface-3";
const PRIMARY_ACTION =
    ACTION_BASE + " bg-accent text-accent-fg border border-transparent pointer-fine:hover:bg-accent-hover";

const SelectionSummary = () => {
    const {
        useObjectInfoStore,
        useSelectedObjectStore,
        useTableNavStore,
        useFeaAnimationStore,
        useTreeViewStore,
    } = useViewerStores();
    const {
        name,
        jsonData,
    } = useObjectInfoStore();
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
    const cbCells = useCellBuilderStore((s) => s.cells);
    const cbAddMode = useCellBuilderStore((s) => s.cellAddMode);
    const hideCells = useCellBuilderStore((s) => s.hideCells);
    const unhideAllCells = useCellBuilderStore((s) => s.unhideAllCells);
    const toggleCellAddMode = useCellBuilderStore((s) => s.toggleCellAddMode);
    const cellCtx = cbActive && cbSelection !== null;
    const cellNames = cbSelectedIds
        .map((id) => cbCells[id]?.name)
        .filter((n): n is string => !!n);

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
    const parentName = React.useMemo(
        () => (cellCtx ? null : parentLevelName()),
        [selectedObjects, treeData, cellCtx],
    );
    const onSelectParent = () => void selectParentLevel();

    // Total drawRangeIds across all selected meshes — that's the
    // count of "things selected" the user thinks of (one per
    // clicked element, regardless of how many meshes back them).
    let rangeCount = 0;
    selectedObjects.forEach((ids) => {
        rangeCount += ids.size;
    });
    // Effective selection identity — cell context takes priority so the panel
    // reads as the cell you clicked, not a result element hidden behind it.
    const displayName = cellCtx
        ? (cbSelection && cbCells[cbSelection.cellId]?.name) || ''
        : name;
    const multiSelectCount = cellCtx ? cbSelectedIds.length : rangeCount;
    const isMultiSelect = multiSelectCount > 1;
    const addModeOn = cellCtx ? cbAddMode : additiveMode;

    // Visibility / selection actions dispatch to the active source. Unhide-all
    // always clears both (regular ranges + cell hides) so "show everything"
    // never leaves something hidden in the other system.
    const onHide = () => (cellCtx ? hideCells(cbSelectedIds) : hideSelectedRanges());
    const onUnhideAll = () => {
        unhideAllRanges();
        if (cbActive) unhideAllCells();
    };
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
        const n = await copySelectionNames(selectedObjects);
        if (n > 0) flashCopied("multi");
    };

    // "Show in data" — visible only when the picked element resolves
    // to a vertex on the active FEA mesh (so the button doesn't
    // appear on unrelated CAD picks). Click opens the data table
    // panel and scrolls to the element's first node.
    const feaSessionActive = useFeaAnimationStore((s) => s.sessionActive);
    const setPanelOpen = useTableNavStore((s) => s.setPanelOpen);
    const setGoToTarget = useTableNavStore((s) => s.setGoToTarget);
    const firstNodeId = name && feaSessionActive ? elementFirstNodeId(name) : null;
    const onShowInData = () => {
        if (firstNodeId == null) return;
        setPanelOpen(true);
        setGoToTarget({kind: "node", id: firstNodeId});
    };

    // Camera actions — desktop has keyboard shortcuts (zoom-fit / center-on-selection), but
    // mobile has no keyboard, so surface them as buttons in this panel. Builder
    // cells are excluded from the fittable scene, so frame them explicitly.
    const onFitAll = () => {
        const s = sceneRef.current, c = cameraRef.current, ctl = controlsRef.current;
        if (!c || !ctl) return;
        if (cbActive && Object.keys(cbCells).length > 0 && frameCells("all", ctl, c)) {
            requestRender();
            return;
        }
        if (s) {
            zoomToAll(s, c, ctl);
            requestRender();
        }
    };
    const onGoToObject = () => {
        const c = cameraRef.current, ctl = controlsRef.current;
        if (!c || !ctl) return;
        if (cellCtx && frameCells(cbSelectedIds, ctl, c)) {
            requestRender();
            return;
        }
        centerViewOnSelection(ctl, c);
        requestRender();
    };

    return (
        // No chrome of its own: the dock (or, in the classic UI, ObjectInfoBoxComponent)
        // owns the box, the heading and the scrolling. A panel that draws its own
        // container is what produced the box-in-a-box the rewrite is removing.
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
                        className={`${PRIMARY_ACTION} rounded-pill`}
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
                            className={SECONDARY_ACTION}
                            title={
                                cellCtx
                                    ? (isMultiSelect ? `Hide ${multiSelectCount} selected cells` : "Hide selected cell")
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
                    {/* Unhide all is a scene-wide recovery action — it stays
                        available as long as any entity remains, even with no
                        selection, so you can always undo a Hide. */}
                    <button
                        type="button"
                        onClick={onUnhideAll}
                        className={SECONDARY_ACTION}
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
                        className={`sm:hidden ${SECONDARY_ACTION}`}
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
                            className={PRIMARY_ACTION}
                            title={`Open the FEA data table and scroll to node ${firstNodeId} (this element's first node)`}
                            aria-label="Show this element in the FEA data table"
                        >
                            <TableIcon/>
                            Show in data
                        </button>
                    )}
                    {/* Camera button — mobile-only (desktop has keyboard
                        shortcuts). "Go to object" frames the current selection;
                        "Fit all" (scene-wide) lives next to Unhide all above. */}
                    <button
                        type="button"
                        onClick={onGoToObject}
                        className={`sm:hidden ${SECONDARY_ACTION}`}
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
                            // A sticky mode, so it reads as an engaged toggle rather
                            // than a button you just pressed.
                            (addModeOn
                                ? ACTION_BASE + " bg-warn-subtle text-warn border border-warn/40"
                                : SECONDARY_ACTION)
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
            {/* ObjectMetadataPanel and CellBuilderSelectionInfo used to render here.
                They are now sibling providers in the Properties registry
                (object-metadata, cellbuilder-cell) so each declares for itself when it
                applies, and a plugin can slot its own detail between them. Their
                bodies are unchanged. */}
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
            "rounded-sm px-1 py-0.5 -mx-1 -my-0.5 pointer-fine:hover:bg-accent-subtle"
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
        className={`shrink-0 mt-0.5 p-1 ${SECONDARY_ACTION}`}
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

export default SelectionSummary;
