import React, {useState} from 'react';
import {useObjectInfoStore} from '@/state/objectInfoStore';
import {useSelectedObjectStore} from '@/state/useSelectedObjectStore';
import {copySelectionNames, writeToClipboard} from '@/utils/clipboard/copySelectionNames';
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
            <div className="table-row">
                <div className="table-cell w-24">Name:</div>
                <div className="table-cell w-48 break-all">{name}</div>
            </div>
            {/* Mobile-only copy buttons. Desktop has Shift+C as the
                canonical multi-name copy and a normal text-select on
                the name cell for single — no need to clutter the
                desktop UI. ``sm:hidden`` hides at ≥ 640 px. The
                multi-select button only appears once more than one
                element is selected so the desktop user who happens
                to be on a narrow window doesn't see two redundant
                buttons. */}
            {name && (
                <div className="sm:hidden flex flex-wrap gap-2 mt-2">
                    <button
                        type="button"
                        onClick={() => void onCopySingle()}
                        className="bg-blue-700 hover:bg-blue-600 text-white text-xs rounded px-3 py-2 min-h-[40px]"
                        aria-label="Copy name to clipboard"
                    >
                        {copied === "single" ? "Copied ✓" : "Copy name"}
                    </button>
                    {isMultiSelect && (
                        <button
                            type="button"
                            onClick={() => void onCopyAll()}
                            className="bg-blue-700 hover:bg-blue-600 text-white text-xs rounded px-3 py-2 min-h-[40px]"
                            aria-label={`Copy all ${multiSelectCount} selected names to clipboard`}
                        >
                            {copied === "multi"
                                ? `Copied ${multiSelectCount} ✓`
                                : `Copy all (${multiSelectCount})`}
                        </button>
                    )}
                </div>
            )}
            <div className="table-row hidden">
                <div className="table-cell w-24">Face Index:</div>
                <div className="table-cell w-48">{faceIndex}</div>
            </div>
            <div className="table-row">
                <div className="table-cell w-24 min-w-24">Clicked @:</div>
                <CoordinateDisplay clickCoordinate={clickCoordinate} prec={prec} />
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

export default ObjectInfoBox;
