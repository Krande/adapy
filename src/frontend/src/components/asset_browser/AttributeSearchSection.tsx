// "Search all collections (attributes)": the attribute search as a section of the Sources tab,
// folded like "Request from a provider" just above it.
//
// ITS OWN SECTION, NOT A TOOLBAR BUTTON. The search spans every collection of every provider in the
// scope; a button beside the tree's collection picker read as if it searched that one collection.
//
// INLINE, WITH A POP-OUT. The panel draws in the tab (its results scroll both ways under the form);
// "Pop out" moves it to the in-viewer floating panel for wide results. One copy at a time: while the
// floating one is open the section says so instead of drawing a second (the state is shared --
// `useAttributeSearchStore` -- so nothing is lost either way).
//
// Whether the section is open is remembered per browser; storage can be blocked, and it then
// simply starts folded.

import React, { Suspense, lazy, useState } from "react";

import { useViewerStores } from "@/state/AdaViewerContext";

const AttributeSearchPanel = lazy(() => import("./AttributeSearchPanel"));

const OPEN_KEY = "ada.assets.attributeSearch.sectionOpen";

function readOpen(): boolean {
    try {
        return globalThis.localStorage?.getItem(OPEN_KEY) === "1";
    } catch {
        return false;
    }
}

function writeOpen(open: boolean): void {
    try {
        globalThis.localStorage?.setItem(OPEN_KEY, open ? "1" : "0");
    } catch {
        // Remembered for this page only.
    }
}

const LINK = "text-blue-300 hover:text-white";

const AttributeSearchSection: React.FC = () => {
    const { useViewerPanelStore } = useViewerStores();
    const floating = useViewerPanelStore((s) => s.open === "search");
    const { openPanel, closePanel } = useViewerPanelStore.getState();
    const [open, setOpen] = useState(readOpen);

    return (
        <details
            open={open}
            onToggle={(e) => {
                const next = e.currentTarget.open;
                if (next !== open) {
                    setOpen(next);
                    writeOpen(next);
                }
            }}
        >
            <summary className="cursor-pointer text-gray-400 select-none">
                Search all collections (attributes)
                <span className="ml-1 text-gray-500">· every provider in this scope</span>
            </summary>
            <div className="mt-1.5">
                {floating ? (
                    <div className="text-gray-400">
                        Open in the floating window.{" "}
                        <button type="button" className={LINK} onClick={closePanel}>
                            Show it here instead
                        </button>
                    </div>
                ) : (
                    <>
                        <div className="flex items-center gap-2 text-gray-500">
                            <span>Finds nodes by the attributes their providers indexed, across every collection.</span>
                            <button
                                type="button"
                                className={`ml-auto shrink-0 ${LINK}`}
                                title="Open the search in a floating window over the viewer -- wider, for many columns"
                                onClick={() => openPanel("search")}
                            >
                                Pop out ↗
                            </button>
                        </div>
                        {open && (
                            <Suspense fallback={<div className="py-2 text-gray-400">Loading…</div>}>
                                <AttributeSearchPanel inline />
                            </Suspense>
                        )}
                    </>
                )}
            </div>
        </details>
    );
};

export default AttributeSearchSection;
