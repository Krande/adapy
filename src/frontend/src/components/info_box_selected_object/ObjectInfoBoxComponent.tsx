import {PanelCard} from "@/components/ui/PanelFrame";
import React, {useState, useSyncExternalStore} from 'react';
import {useSelectionCount} from "@/viewport/api/useViewportSelection";
import {ErrorBoundary} from '@/components/common/ErrorBoundary';
import {
    listPropertyProviders,
    providersFor,
    subscribePropertyProviders,
    type PropertyProvider,
    type SelectionSnapshot,
} from '@/plugins/propertyProviders';
import {useViewerStores} from '@/state/AdaViewerContext';
import {copySelectionNames, writeToClipboard} from '@/utils/clipboard/copySelectionNames';
import {elementFirstNodeId} from '@/utils/scene/fea/goToNode';

import {useCellBuilderStore} from '@/state/cellBuilderStore';
import {selectedRef, selectionName, selectionPlace} from '@/utils/cellbuilder/boxTarget';
import {useSceneMirrorStore} from '@/state/sceneMirrorStore';
import {parentLevelName} from '@/utils/tree_view/treeSelection';
import {isMirroredWindow, useSceneActions} from '@/layout/sceneActions';
import {activeShellMode, subscribeShellModes} from '@/layout/shellModes';
import ObjectMetadataPanel, {useClickedPosition} from './ObjectMetadataPanel';
import {meshStatsLine, useSelectionMeshStats} from './MeshStatsSection';
import TreeNodeInfoSection from './TreeNodeInfoSection';
import SourceJumpSection from './SourceJumpSection';
import IndexedAttributesSection from './IndexedAttributesSection';
import RelatedSection from './RelatedSection';
import {BuilderItemSection, BuilderStructureSection} from './cellBuilderSelection/BuilderSections';
import FemConceptSelectionInfo from './FemConceptSelectionInfo';
import FeaSetsSection from './FeaSetsSection';
import FeaElementSection from './FeaElementSection';
import DownloadSelectionButton from './DownloadSelectionButton';
import {builderCellsShown} from '@/state/cellbuilder/builderVisibility';
import {onBeforeDraw} from '@/viewport/api/beforeDraw';

/**
 * Whether a builder pick is the panel's: the builder's cells are on screen (or no builder scene says
 * otherwise). A shell may keep the procedural model open behind a mode that hides them -- the docked
 * UI's Output, its FEM modes --, and a cell or ref picked back in Input is no selection there: no
 * summary for it, no section. Followed frame by frame, as a mode hides the builder's layer after
 * it has switched.
 */
function useBuilderPickShown(): boolean {
    const [, redraw] = React.useReducer((n: number) => n + 1, 0);
    const shown = builderCellsShown() !== false;
    const seen = React.useRef(shown);
    seen.current = shown;
    React.useEffect(
        () =>
            onBeforeDraw(() => {
                if ((builderCellsShown() !== false) !== seen.current) redraw();
            }),
        [],
    );
    return shown;
}

// 1500 ms is the smallest hold that still feels intentional vs a
// reflexive tap-and-release; long enough that "Copied" lingers on
// screen for a beat after the haptic-style feedback most users
// expect from a copy gesture.
const COPIED_FEEDBACK_MS = 1500;

/** Options of the Selected Object panel's content. Absent, each leaves the panel as it is. */
export interface ObjectInfoBodyProps {
    /** Replaces what "Show in data" does (open the FEA data table at the node), given the
     *  picked element's first node. */
    onShowInData?: (nodeId: number) => void;
    /**
     * Which of the matching sections to draw, by provider id (core's and the plugins'), asked
     * after their own `match`. A shell sets it per mode (`PanelOverride.options` as a function
     * of the mode) so, say, the builder's sections stay in the mode that edits the model.
     * A throw keeps the section. Absent: every matching section.
     */
    sections?: (providerId: string, sel: SelectionSnapshot) => boolean;
    /**
     * The picked element's properties (`object-metadata`) as plain rows: no foldable
     * "Properties" heading, always open, and its clicked-at decimals in that row's own menu.
     * For a shell whose panel is itself the properties. Absent: folded under a heading, as before.
     */
    flat?: boolean;
    /**
     * False: with a builder item picked, the summary at the top (its name again, Hide, Unhide
     * all, Add mode) is left out -- the item's own layout names it, and a shell's Model panel
     * and toolbar act on it. A mesh pick keeps its summary. Absent or true: drawn, as before.
     */
    builderSummary?: boolean;
    /**
     * The summary at the top of the panel. "full" (the default): as it always was -- the name row,
     * Hide / Hide others / Unhide all, Show in data, Download as…, its notices, and in the object
     * metadata the "Clicked at" row and the "Mesh (n tris)" section. "menu": none of those; one
     * small "⋯" button at the top right of the body instead, whose menu holds Show in data,
     * Download as… (greyed out, the reason its tooltip, when there is no source), Copy clicked
     * position, and the mesh's stats as a line. For a flat panel whose items name what is picked
     * themselves, with the tree and the keys to hide it.
     */
    summary?: "full" | "menu";
}

const ObjectInfoOptions = React.createContext<ObjectInfoBodyProps>({});

/** The selection's snapshot for the providers' `match`; cell context wins, as in the summary. */
function useSelectionSnapshot(): SelectionSnapshot {
    const {useObjectInfoStore, useSelectedObjectStore, useTreeViewStore} = useViewerStores();
    const name = useObjectInfoStore((s) => s.name);
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    const treeData = useTreeViewStore((s) => s.treeData);
    const cbActive = useCellBuilderStore((s) => s.active !== null);
    const cbSelection = useCellBuilderStore((s) => s.selection);
    const cbSelectedIds = useCellBuilderStore((s) => s.selectedCellIds);
    const cbCells = useCellBuilderStore((s) => s.cells);
    const cbRefs = useCellBuilderStore((s) => s.refs);
    const cbGroups = useCellBuilderStore((s) => s.groups);
    const cbLoftMembers = useCellBuilderStore((s) => s.loftMembers);
    const cbActiveGroup = useCellBuilderStore((s) => s.activeGroup);
    const mirroredCount = useSelectedRangeCount();
    const builderShown = useBuilderPickShown();
    // The shell's mode, so a section (or the shell's `sections`) can keep to the modes it is for.
    const shellMode = useSyncExternalStore(subscribeShellModes, activeShellMode, activeShellMode);
    return React.useMemo<SelectionSnapshot>(() => {
        const rangeCount = mirroredCount;
        const base = {
            meshName: name || null,
            hasEntities: treeData != null || (cbActive && Object.keys(cbCells).length > 0),
            cellBuilderActive: cbActive,
            ...(shellMode ? {shellMode} : {}),
        };
        if (cbActive && cbSelection !== null && builderShown) {
            // Where the pick is, so a plugin's section can place itself (and the builder's
            // space and structure sections know what to show).
            const place = selectionPlace(cbSelection, {cells: cbCells, refs: cbRefs, groups: cbGroups, loftMembers: cbLoftMembers});
            const name = selectionName(cbSelection, cbCells, cbRefs);
            const ref = selectedRef(cbSelection, cbRefs);
            if (ref) return {...base, ...place, kind: "ref", name, refOwner: ref.owner, refKey: ref.key, count: 1};
            return {...base, ...place, kind: "cell", name, count: cbSelectedIds.length};
        }
        if (rangeCount > 0 || name) return {...base, kind: "mesh", name: name || null, count: rangeCount};
        // Nothing picked in an open model: the active structure still has its section.
        const structure = cbActive && cbActiveGroup && cbGroups.some((g) => g.name === cbActiveGroup) ? cbActiveGroup : undefined;
        return {...base, kind: "none", name: null, count: 0, ...(structure ? {structure} : {})};
    }, [name, selectedObjects, treeData, cbActive, cbSelection, cbSelectedIds, cbCells, cbRefs, cbGroups, cbLoftMembers, cbActiveGroup, mirroredCount, shellMode, builderShown]);
}

/**
 * How many draw ranges are selected: counted here in the main window; in a
 * pop-out window, which has no 3D selection, the main window's count
 * (`core:selection`).
 */
function useSelectedRangeCount(): number {
    // On the viewport on screen, whichever it is.
    const selected = useSelectionCount();
    const mirrored = useSceneMirrorStore((s) => s.rangeCount);
    return isMirroredWindow() ? mirrored : selected;
}

/** ObjectMetadataPanel takes its payload as a prop. */
const ObjectMetadata = () => {
    const {useObjectInfoStore} = useViewerStores();
    const {flat, summary} = React.useContext(ObjectInfoOptions);
    const jsonData = useObjectInfoStore((s) => s.jsonData);
    return <ObjectMetadataPanel data={jsonData} flat={flat} menuSummary={summary === "menu"}/>;
};

/** The FEA sets section, in the flat layout's style when the panel is flat. */
const FeaSets: React.FC = () => <FeaSetsSection flat={!!React.useContext(ObjectInfoOptions).flat}/>;

/** The picked FEA element in the flat layout; nothing in the classic panel, which shows it folded. */
const FlatFeaElement: React.FC<{name: string | null}> = ({name}) => {
    const {flat} = React.useContext(ObjectInfoOptions);
    return flat ? <FeaElementSection name={name}/> : null;
};

/** `providersFor`'s list, kept to those the shell's `sections` lets through; a throwing filter keeps the section. */
export function visibleSections(
    list: readonly PropertyProvider[],
    sel: SelectionSnapshot,
    sections: ObjectInfoBodyProps["sections"],
): PropertyProvider[] {
    if (!sections) return [...list];
    return list.filter((p) => {
        try {
            return sections(p.id, sel);
        } catch (err) {
            console.warn(`[properties] sections filter threw for "${p.id}" -- kept`, err);
            return true;
        }
    });
}

const notCell = (sel: SelectionSnapshot) => sel.kind !== "cell" && sel.kind !== "ref";
const always = () => true;
const builderPick = (sel: SelectionSnapshot) => sel.cellBuilderActive && (sel.kind === "cell" || sel.kind === "ref");

/** Core's own sections, as providers, in the order the panel has always drawn them. */
export const CORE_PROPERTY_PROVIDERS: readonly PropertyProvider[] = [
    // The name and the actions. Also with nothing selected while the scene holds anything:
    // Unhide all / Fit all must stay reachable after the last selection was hidden.
    {id: "selection-summary", order: 0, match: (sel) => sel.kind !== "none" || sel.hasEntities, render: () => <SelectionSummary/>},
    // Selected FEM concepts (masses, supports, loads, equipment): drawn on the FEM
    // overlay, not tree rows, so they have their own section.
    {id: "fem-concepts", order: 10, match: always, render: () => <FemConceptSelectionInfo/>},
    // Where an element of an exported file came from, and a jump to that node in the
    // Sources tree (switching scope when it lives in another). Any loaded model whose
    // file has a provenance record; nothing for others. Above Properties, which starts
    // folded.
    {id: "source-jump", order: 20, match: notCell, render: () => <SourceJumpSection/>},
    // Renders for any picked element -- even without a server-side jsonData payload --
    // because it also reads metadata from the lineage store (GLB extension's
    // ``object_metadata`` field) AND hosts the clicked-coordinate row.
    {id: "object-metadata", order: 30, match: (sel) => sel.meshName != null, render: () => <ObjectMetadata/>},
    // A picked element of the loaded FEA result: its property values and the element sets
    // naming it, each isolatable. Nothing for a pick no set or property names.
    // A picked FEA element's own data (type, nodes, section, thickness, material) in the flat
    // layout, with an ELEMENT badge; the classic panel has the same in its folded Properties rows.
    {id: "fea-element", order: 32, match: (sel) => sel.kind === "mesh" && /^E\d+$/.test(sel.meshName ?? ""), render: (sel) => <FlatFeaElement name={sel.meshName}/>},
    {id: "fea-sets", order: 35, match: (sel) => sel.kind === "mesh" && /^E\d+$/.test(sel.meshName ?? ""), render: () => <FeaSets/>},
    // The selected row's attributes from the attribute index; nothing when it has none.
    {id: "indexed-attributes", order: 40, match: notCell, render: () => <IndexedAttributesSection/>},
    // Its system, run and related elements from the relations index; nothing without.
    {id: "related", order: 50, match: notCell, render: () => <RelatedSection/>},
    // Structural facts about the selected hierarchy node. Renders for any tree selection,
    // and is the ONLY section a spatial container can populate -- everything above is
    // keyed on geometry the container does not have.
    {id: "tree-node", order: 60, match: always, render: () => <TreeNodeInfoSection/>},
    // Procedural cellbuilder selection detail (cellBuilderSelection/BuilderSections), here, not
    // in the busy cellbuilder tool panel: the picked item as one layout -- where it is (its
    // space and structure), what it is, its box and its fields, or a plugin's ref --, and a
    // structure on its own (the active one, with nothing picked). A plugin's section slots
    // before, between or after them by order.
    {id: "cellbuilder-item", order: 70, match: builderPick, render: () => <BuilderItemSection/>},
    {id: "cellbuilder-structure", order: 74, match: (sel) => sel.cellBuilderActive && sel.kind === "none" && !!sel.structure, render: () => <BuilderStructureSection/>},
];

/**
 * The Selected Object Info panel's content: the selection's name, actions and
 * every section that describes it -- core's and any a plugin registered
 * (`registerPropertyProvider`), each in its own error boundary. Its frame (the
 * card and heading) is `ObjectInfoBox` below; another host may draw its own.
 */
export const ObjectInfoBody: React.FC<ObjectInfoBodyProps> = ({onShowInData, sections, flat, builderSummary = true, summary = "full"}) => {
    const sel = useSelectionSnapshot();
    const plugins = useSyncExternalStore(subscribePropertyProviders, listPropertyProviders, listPropertyProviders);
    const options = React.useMemo(() => ({onShowInData, flat, summary}), [onShowInData, flat, summary]);
    return (
        <ObjectInfoOptions.Provider value={options}>
            {visibleSections(providersFor(sel, CORE_PROPERTY_PROVIDERS, plugins), sel, sections)
                .filter((p) => builderSummary || p.id !== "selection-summary" || (sel.kind !== "cell" && sel.kind !== "ref"))
                .map((p) => (
                    <ErrorBoundary key={p.id} label={p.owner ? `${p.owner} (${p.id})` : p.id}>
                        {p.render(sel)}
                    </ErrorBoundary>
                ))}
        </ObjectInfoOptions.Provider>
    );
};

/** The selection's name and actions. */
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

const SelectionSummary = () => {
    const {onShowInData: showInData, summary: summaryMode} = React.useContext(ObjectInfoOptions);
    // What the buttons do: on this window's scene, or -- in a pop-out window --
    // sent to the main window, which has it (`@/layout/sceneActions`).
    const actions = useSceneActions();
    const mirrored = isMirroredWindow();
    const mirroredParent = useSceneMirrorStore((s) => s.parentName);
    const {
        useObjectInfoStore,
        useSelectedObjectStore,
        useTableNavStore,
        useFeaAnimationStore,
        useTreeViewStore,
    } = useViewerStores();
    const name = useObjectInfoStore((s) => s.name);
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
    const cbRefs = useCellBuilderStore((s) => s.refs);
    const cbAddMode = useCellBuilderStore((s) => s.cellAddMode);
    const hideCells = useCellBuilderStore((s) => s.hideCells);
    const toggleCellAddMode = useCellBuilderStore((s) => s.toggleCellAddMode);
    // A builder pick behind a mode that does not show the cells is not this panel's (useBuilderPickShown).
    const builderShown = useBuilderPickShown();
    const cellCtx = cbActive && cbSelection !== null && builderShown;
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
    // Effective selection identity — cell context takes priority so the panel
    // reads as the cell you clicked, not a result element hidden behind it.
    const displayName = cellCtx
        ? selectionName(cbSelection, cbCells, cbRefs) || ''
        : name;
    const multiSelectCount = cellCtx ? cbSelectedIds.length : rangeCount;
    const isMultiSelect = multiSelectCount > 1;
    const addModeOn = cellCtx ? cbAddMode : additiveMode;

    // Visibility / selection actions dispatch to the active source. Unhide-all
    // always clears both (regular ranges + cell hides) so "show everything"
    // never leaves something hidden in the other system (`actions.showAll`).
    const onHide = () => (cellCtx ? hideCells(cbSelectedIds) : actions.hideSelection());
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
    const firstNodeId = name && feaSessionActive ? elementFirstNodeId(name) : null;
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

/** The classic frame: the menu-column card, capped in height so a long record scrolls inside. */
const ObjectInfoBox = () => (
    <PanelCard className="min-w-80 max-h-[80svh] overflow-y-auto" heading="Selected Object Info">
        <ObjectInfoBody/>
    </PanelCard>
);

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

export default ObjectInfoBox;
