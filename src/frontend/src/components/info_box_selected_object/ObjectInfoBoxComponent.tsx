import React, {useState} from 'react';
import {useObjectInfoStore} from '@/state/objectInfoStore';
import {useSelectedObjectStore} from '@/state/useSelectedObjectStore';
import {copySelectionNames, writeToClipboard} from '@/utils/clipboard/copySelectionNames';
import {hideSelectedRanges, unhideAllRanges} from '@/utils/scene/visibility';
import JsonViewerComponent from './JsonViewerComponent';
import CoordinateDisplay from "./CoordinateDisplay";

// 1500 ms is the smallest hold that still feels intentional vs a
// reflexive tap-and-release; long enough that "Copied" lingers on
// screen for a beat after the haptic-style feedback most users
// expect from a copy gesture.
const COPIED_FEEDBACK_MS = 1500;

const ObjectInfoBox = () => {
    const {
        name,
        faceIndex,
        clickCoordinate,
        jsonData,
        isJsonViewVisible,
        setIsJsonViewVisible,
    } = useObjectInfoStore();
    const selectedObjects = useSelectedObjectStore((s) => s.selectedObjects);
    // Total drawRangeIds across all selected meshes — that's the
    // count of "things selected" the user thinks of (one per
    // clicked element, regardless of how many meshes back them).
    let multiSelectCount = 0;
    selectedObjects.forEach((ids) => {
        multiSelectCount += ids.size;
    });
    const isMultiSelect = multiSelectCount > 1;

    const [copied, setCopied] = useState<"single" | "multi" | null>(null);
    // Mobile-only collapse state for the "Clicked @" coords. Default
    // closed because they take a noticeable chunk of screen real
    // estate and the user rarely needs the literal click point — the
    // selection name is what matters most after a tap.
    const [showCoords, setShowCoords] = useState(false);
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

    const toggleJsonView = () => {
        setIsJsonViewVisible(!isJsonViewVisible);
    };
    const prec = 3;
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
                        className="bg-blue-700/80 hover:bg-blue-600 active:bg-blue-800 text-white text-[11px] rounded-full px-2 py-0.5"
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
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded px-2 py-1"
                        title={
                            isMultiSelect
                                ? `Hide ${multiSelectCount} selected (Shift+H)`
                                : "Hide selected (Shift+H)"
                        }
                        aria-label="Hide selected geometry"
                    >
                        🚫 Hide
                        {isMultiSelect ? ` (${multiSelectCount})` : ""}
                    </button>
                    <button
                        type="button"
                        onClick={() => unhideAllRanges()}
                        className="bg-gray-700 hover:bg-gray-600 active:bg-gray-800 text-white text-[11px] rounded px-2 py-1"
                        title="Unhide every hidden draw range across the scene (Shift+U)"
                        aria-label="Unhide all geometry"
                    >
                        👁 Unhide all
                    </button>
                </div>
            )}
            <div className="table-row hidden">
                <div className="table-cell w-24">Face Index:</div>
                <div className="table-cell w-48">{faceIndex}</div>
            </div>
            {/* Click coordinate. Same mobile/desktop split as the
                Name row: desktop keeps the fixed-width label
                column; mobile collapses behind a chevron because
                the literal click XYZ is rarely the thing the user
                came to the panel for, and the three numbers cost
                more vertical space than the name itself. */}
            <div className="hidden sm:table-row">
                <div className="table-cell w-24 min-w-24">Clicked @:</div>
                <CoordinateDisplay clickCoordinate={clickCoordinate} prec={prec} />
            </div>
            <div className="sm:hidden mt-2">
                <button
                    type="button"
                    onClick={() => setShowCoords((v) => !v)}
                    className="flex items-center gap-1 text-[11px] text-gray-200 hover:text-white"
                    aria-expanded={showCoords}
                    aria-controls="object-info-click-coord"
                >
                    <span className="inline-block w-3">{showCoords ? "▾" : "▸"}</span>
                    <span>Clicked at</span>
                </button>
                {showCoords && (
                    <div id="object-info-click-coord" className="mt-1 ml-4 table">
                        <div className="table-row">
                            <CoordinateDisplay
                                clickCoordinate={clickCoordinate}
                                prec={prec}
                            />
                        </div>
                    </div>
                )}
            </div>
            {jsonData && (
                <div className="table-row">
                    <div className="table-cell w-24">JSON Data:</div>
                    <div className="table-cell w-48">
                        <button
                            className="bg-blue-500 text-white px-2 py-1 rounded"
                            onClick={toggleJsonView}
                        >
                            {isJsonViewVisible ? 'Hide JSON' : 'Show JSON'}
                        </button>
                    </div>
                </div>
            )}
            {isJsonViewVisible && jsonData && (
                <div className="mt-2">
                    <JsonViewerComponent data={jsonData}/>
                </div>
            )}
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

export default ObjectInfoBox;
