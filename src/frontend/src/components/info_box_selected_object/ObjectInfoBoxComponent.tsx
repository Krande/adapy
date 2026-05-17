import React, {useState} from 'react';
import {useObjectInfoStore} from '@/state/objectInfoStore';
import {useSelectedObjectStore} from '@/state/useSelectedObjectStore';
import {useTableNavStore} from '@/state/tableNavStore';
import {useFeaAnimationStore} from '@/state/feaAnimationStore';
import {copySelectionNames, writeToClipboard} from '@/utils/clipboard/copySelectionNames';
import {hideSelectedRanges, unhideAllRanges} from '@/utils/scene/visibility';
import {elementFirstNodeId} from '@/utils/scene/fea/goToNode';
import ObjectMetadataPanel from './ObjectMetadataPanel';

// 1500 ms is the smallest hold that still feels intentional vs a
// reflexive tap-and-release; long enough that "Copied" lingers on
// screen for a beat after the haptic-style feedback most users
// expect from a copy gesture.
const COPIED_FEEDBACK_MS = 1500;

const ObjectInfoBox = () => {
    const {
        name,
        faceIndex,
        jsonData,
    } = useObjectInfoStore();
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    const additiveMode = useSelectedObjectStore((s) => s.additiveMode);
    const toggleAdditiveMode = useSelectedObjectStore((s) => s.toggleAdditiveMode);
    // Total drawRangeIds across all selected meshes — that's the
    // count of "things selected" the user thinks of (one per
    // clicked element, regardless of how many meshes back them).
    let multiSelectCount = 0;
    selectedObjects.forEach((ids) => {
        multiSelectCount += ids.size;
    });
    const isMultiSelect = multiSelectCount > 1;

    const [copied, setCopied] = useState<"single" | "multi" | null>(null);
    const flashCopied = (which: "single" | "multi") => {
        setCopied(which);
        window.setTimeout(() => setCopied((c) => (c === which ? null : c)), COPIED_FEEDBACK_MS);
    };

    const onCopySingle = async () => {
        if (!name) return;
        const ok = await writeToClipboard(name);
        if (ok) flashCopied("single");
    };
    const onCopyAll = async () => {
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

    return (
        <div className="bg-gray-400 bg-opacity-50 rounded p-2 min-w-80">
            <h2 className="font-bold">Selected Object Info</h2>
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
            {name && (
                <>
                    <div className="hidden sm:table-row">
                        <div className="table-cell w-24">Name:</div>
                        <div className="table-cell w-48 break-all">
                            <NameCopyButton
                                name={name}
                                copied={copied === "single"}
                                onCopy={onCopySingle}
                            />
                        </div>
                    </div>
                    <div className="sm:hidden break-all">
                        <NameCopyButton
                            name={name}
                            copied={copied === "single"}
                            onCopy={onCopySingle}
                        />
                    </div>
                </>
            )}
            {/* Multi-select copy: a small inline pill, mobile-only.
                Desktop uses Shift+C — adding a pill there would be
                redundant. Only renders when > 1 element is selected
                so single-tap selections aren't cluttered. */}
            {name && isMultiSelect && (
                <div className="sm:hidden mt-1">
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
            {name && (
                <div className="mt-2 flex flex-wrap gap-2 items-center">
                    <button
                        type="button"
                        onClick={() => hideSelectedRanges()}
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded px-2 py-1 inline-flex items-center gap-1"
                        title={
                            isMultiSelect
                                ? `Hide ${multiSelectCount} selected (Shift+H)`
                                : "Hide selected (Shift+H)"
                        }
                        aria-label="Hide selected geometry"
                    >
                        <EyeOffIcon/>
                        Hide
                        {isMultiSelect ? ` (${multiSelectCount})` : ""}
                    </button>
                    <button
                        type="button"
                        onClick={() => unhideAllRanges()}
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded px-2 py-1 inline-flex items-center gap-1"
                        title="Unhide every hidden draw range across the scene (Shift+U)"
                        aria-label="Unhide all geometry"
                    >
                        <EyeIcon/>
                        Unhide all
                    </button>
                    {firstNodeId != null && (
                        <button
                            type="button"
                            onClick={onShowInData}
                            className="bg-blue-700 hover:bg-blue-600 active:bg-blue-800 text-white text-[11px] rounded px-2 py-1 inline-flex items-center gap-1"
                            title={`Open the FEA data table and scroll to node ${firstNodeId} (this element's first node)`}
                            aria-label="Show this element in the FEA data table"
                        >
                            <TableIcon/>
                            Show in data
                        </button>
                    )}
                    {/* Additive selection toggle. Mobile-only: desktop
                        users have Shift+click for the same effect, and
                        adding a chrome button there would be redundant.
                        Sticky — stays on across taps and even across
                        deselects, so a multi-pick session is uninterrupted
                        once enabled. Tap again to turn off. */}
                    <button
                        type="button"
                        onClick={toggleAdditiveMode}
                        className={
                            "sm:hidden text-[11px] rounded px-2 py-1 text-white " +
                            (additiveMode
                                ? "bg-amber-600 hover:bg-amber-500 active:bg-amber-700"
                                : "bg-gray-700 hover:bg-gray-600 active:bg-gray-800")
                        }
                        title={
                            additiveMode
                                ? "Tapping objects adds to selection. Tap to switch back to single-select."
                                : "Switch to multi-select: subsequent taps will add to the selection instead of replacing it."
                        }
                        aria-pressed={additiveMode}
                        aria-label={additiveMode ? "Disable add-to-selection" : "Enable add-to-selection"}
                    >
                        {additiveMode ? "✓ Add mode on" : "+ Add mode"}
                    </button>
                </div>
            )}
            <div className="table-row hidden">
                <div className="table-cell w-24">Face Index:</div>
                <div className="table-cell w-48">{faceIndex}</div>
            </div>
            {/* The Properties panel renders for any selection — even
                without a server-side jsonData payload — because it
                also reads metadata from the lineage store (GLB
                extension's ``object_metadata`` field) AND hosts the
                clicked-coordinate row that used to live above. */}
            {name && <ObjectMetadataPanel data={jsonData}/>}
        </div>
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
            "rounded px-1 py-0.5 -mx-1 -my-0.5 hover:bg-blue-700/40 active:bg-blue-700/60"
        }
        aria-label="Copy name to clipboard"
        title="Tap to copy"
    >
        {copied ? `${name} ✓` : name}
    </button>
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
