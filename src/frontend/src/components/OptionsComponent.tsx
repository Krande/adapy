import React, {Suspense, useEffect, useState} from "react";
import {runtime} from "@/runtime/config";
import {useOptionsStore} from "@/state/optionsStore";
import ActionButtons from "./options/ActionButtons";
import PointSizeOptions from "./options/PointSizeOptions";
import DisplayOptions from "./options/DisplayOptions";
import ExperimentalOptions from "./options/ExperimentalOptions";
import ShortcutsModal from "./options/ShortcutsModal";

// REST-only controls (scope picker, signed-in row, admin button) live
// here so they're reachable on phones and don't crowd the top bar.
// Lazy-loaded so the desktop bundle stays slim.
const RestSection = React.lazy(() => import("./options/RestSection"));

const MOBILE_QUERY = "(max-width: 767px)";

// Lightweight disclosure. State is local — these sections are leaf-y
// enough that no other component cares whether they're open. Defaults
// to closed; click the header to toggle.
const CollapsibleSection: React.FC<{title: string; children: React.ReactNode}> = ({
    title,
    children,
}) => {
    const [open, setOpen] = useState(false);
    return (
        <div>
            <button
                type="button"
                className="w-full flex items-center justify-between py-1 text-left font-semibold"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
            >
                <span>{title}</span>
                <span className="text-gray-300 text-xs">{open ? "▾" : "▸"}</span>
            </button>
            {open && <div className="mt-2">{children}</div>}
        </div>
    );
};

// Two layouts:
// * Mobile: full-height slide-in drawer from the left edge. There
//   isn't enough room on a phone for an inline box; the drawer takes
//   over so the user can scroll through the options.
// * Desktop: an inline panel in the menu's info-box column, styled
//   like ObjectInfoBox / GroupInfoBox / StorageBrowser — same
//   semi-transparent gray, same rounded corners. The ☰ button toggles
//   it; nothing else needs to.
function OptionsComponent() {
    const [isMobile, setIsMobile] = useState(
        () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches,
    );
    const setIsOptionsVisible = useOptionsStore((s) => s.setIsOptionsVisible);

    const unique_version_id = runtime.uniqueVersionId();

    useEffect(() => {
        const mq = window.matchMedia(MOBILE_QUERY);
        const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, []);

    const sections = (
        <>
            {runtime.isRestMode() && (
                <>
                    <Suspense fallback={null}>
                        <RestSection/>
                    </Suspense>
                    <hr className="border-gray-600"/>
                </>
            )}
            {/* Closed by default — debug print, URDF load, screenshot
                aren't first-tier actions for everyday users; they only
                matter when you actually need them. */}
            <CollapsibleSection title="Actions">
                <ActionButtons/>
            </CollapsibleSection>
            <hr className="border-gray-600"/>
            {/* Same idea for the scene-config knobs. The defaults are
                tuned for the common case; tucking the controls behind a
                disclosure keeps the drawer from feeling overwhelming. */}
            <CollapsibleSection title="Scene config">
                <div className="space-y-4">
                    <PointSizeOptions/>
                    <DisplayOptions/>
                </div>
            </CollapsibleSection>
            <hr className="border-gray-600"/>
            <ExperimentalOptions/>
            <hr className="border-gray-600"/>
            <ShortcutsModal/>
        </>
    );

    if (isMobile) {
        // Drawer pinned to the left edge, full visible-viewport height.
        return (
            <div className="fixed inset-y-0 left-0 z-40 w-[85vw] max-w-sm bg-gray-800 text-white text-sm shadow-xl flex flex-col">
                <div className="flex items-center justify-between p-3 border-b border-gray-700">
                    <span className="font-bold text-base">Options</span>
                    <button
                        type="button"
                        onClick={() => setIsOptionsVisible(false)}
                        className="text-gray-300 hover:text-white text-xl leading-none px-2"
                        aria-label="Close options"
                        title="Close"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 overflow-y-auto p-4 flex flex-col space-y-4">
                    <div className="text-xs text-gray-300">Version: {unique_version_id}</div>
                    {sections}
                </div>
            </div>
        );
    }

    // Desktop: matches the other info boxes (Info / Group / Storage)
    // visually so the user reads the menu-bar column as one consistent
    // pattern. Cap max-height so a long Options list doesn't push the
    // page; scroll inside instead.
    return (
        <div
            className="bg-gray-400 bg-opacity-50 rounded p-2 min-w-80 max-w-sm text-white text-sm space-y-3 max-h-[70vh] overflow-y-auto"
        >
            <h2 className="font-bold">Options</h2>
            <div className="text-xs text-gray-300">Version: {unique_version_id}</div>
            {sections}
        </div>
    );
}

export default OptionsComponent;
