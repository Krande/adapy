import React, {Suspense, lazy, useEffect, useState} from "react";
import {Rnd} from "react-rnd";
import {useViewerPanelStore, ViewerPanel} from "@/state/viewerPanelStore";

// In-viewer modal host for the Admin and Convert panels. Mounting
// them as a draggable Rnd window keeps the 3D model on-screen while
// the operator pokes at admin tabs or kicks off a conversion — no
// context switch, no losing the camera state.
//
// Layout mode is responsive:
// * Desktop (≥768px): draggable + resizable Rnd over the viewer.
// * Mobile (<768px): fixed full-screen overlay. Drag-resizing a
//   ~360px-wide window on a touch screen is hostile; the standard
//   mobile pattern is "panel takes over." The drag handle goes
//   away, but the same header buttons (close, open in new tab)
//   stay in place.
//
// The dedicated path-mounted ``/admin`` and ``/convert`` routes
// remain available for direct URL navigation and for "open in
// external" tap-targets in the modal header (the arrow-out icon
// below).

const AdminPanel = lazy(() => import("@/components/admin/AdminPanel"));
const ConvertPage = lazy(() => import("@/components/convert/ConvertPage"));

const PANEL_TITLE: Record<ViewerPanel, string> = {
    admin: "Admin panel",
    convert: "Convert files",
};

const PANEL_PATH: Record<ViewerPanel, string> = {
    admin: "/admin",
    convert: "/convert",
};

const MOBILE_QUERY = "(max-width: 767px)";

function useIsMobile(): boolean {
    const [isMobile, setIsMobile] = useState(
        () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches,
    );
    useEffect(() => {
        const mq = window.matchMedia(MOBILE_QUERY);
        const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, []);
    return isMobile;
}

const HeaderBar: React.FC<{
    title: string;
    onExternal: () => void;
    onClose: () => void;
    draggable: boolean;
}> = ({title, onExternal, onClose, draggable}) => (
    <div
        className={
            (draggable ? "viewer-panel-drag-handle cursor-move " : "") +
            "flex items-center gap-2 bg-gray-800 text-gray-100 px-3 py-2 " +
            "border-b border-gray-700 shrink-0"
        }
    >
        <span className="font-medium text-sm truncate">{title}</span>
        <div className="ml-auto flex items-center gap-1">
            <button
                type="button"
                onClick={onExternal}
                title="Open in dedicated tab"
                aria-label="Open in dedicated tab"
                className={
                    "shrink-0 inline-flex items-center justify-center " +
                    "w-9 h-9 sm:w-7 sm:h-7 rounded-sm border border-gray-600 " +
                    "bg-gray-700/60 text-gray-200 hover:bg-gray-600 " +
                    "hover:border-gray-400 hover:text-white"
                }
                onMouseDown={(e) => e.stopPropagation()}
                onTouchStart={(e) => e.stopPropagation()}
            >
                <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                    <path
                        d="M9 2 H14 V7 M14 2 L8 8 M11 9 V13 H3 V5 H7"
                        stroke="currentColor"
                        strokeWidth="1.5"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        fill="none"
                    />
                </svg>
            </button>
            <button
                type="button"
                onClick={onClose}
                title="Close"
                aria-label="Close panel"
                className={
                    "shrink-0 inline-flex items-center justify-center " +
                    "w-9 h-9 sm:w-7 sm:h-7 rounded-sm border border-gray-600 " +
                    "bg-gray-700/60 text-gray-200 hover:bg-gray-600 " +
                    "hover:border-gray-400 hover:text-white"
                }
                onMouseDown={(e) => e.stopPropagation()}
                onTouchStart={(e) => e.stopPropagation()}
            >
                <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                    <path
                        d="M4 4 L12 12 M12 4 L4 12"
                        stroke="currentColor"
                        strokeWidth="2"
                        strokeLinecap="round"
                        fill="none"
                    />
                </svg>
            </button>
        </div>
    </div>
);

const InViewerPanelHost: React.FC = () => {
    const open = useViewerPanelStore((s) => s.open);
    const adminTab = useViewerPanelStore((s) => s.adminTab);
    const closePanel = useViewerPanelStore((s) => s.closePanel);
    const isMobile = useIsMobile();

    if (open === null) return null;

    const openInNewTab = () => {
        // Carry the embedded tab into the dedicated route so "open in new tab" lands on the
        // same tab the floating panel was showing (the full-page admin reads it from the hash).
        const hash = open === "admin" && adminTab ? `#${adminTab}` : "";
        window.open(PANEL_PATH[open] + hash, "_blank", "noopener");
        closePanel();
    };

    const body = (
        <div className="h-full w-full flex flex-col bg-gray-900 border border-gray-700 sm:rounded-sm shadow-2xl overflow-hidden">
            <HeaderBar
                title={PANEL_TITLE[open]}
                onExternal={openInNewTab}
                onClose={closePanel}
                draggable={!isMobile}
            />
            <div className="flex-1 min-h-0 overflow-hidden">
                <Suspense fallback={
                    <div className="p-4 text-sm text-gray-400">Loading…</div>
                }>
                    {open === "admin" && <AdminPanel embedded initialTab={adminTab ?? undefined}/>}
                    {open === "convert" && <ConvertPage/>}
                </Suspense>
            </div>
        </div>
    );

    if (isMobile) {
        // Fixed full-screen overlay. ``100dvh`` so iOS Safari's
        // dynamic toolbar doesn't crop the bottom edge. z-60 keeps
        // it above the canvas (z-0) and below the toast slot (z-50,
        // intentionally inverted on mobile so the panel can show
        // the same toasts the user already saw in the drawer).
        return (
            <div
                className="fixed inset-0 z-[60] w-screen overflow-hidden"
                style={{height: "100dvh"}}
            >
                {body}
            </div>
        );
    }

    return (
        <Rnd
            default={{
                // Centered-ish over the viewport. Big enough that
                // the admin tabs + audit-runs grid don't feel
                // claustrophobic; the user can drag-resize as they
                // like.
                x: Math.max(0, (window.innerWidth - 1100) / 2),
                y: 60,
                width: Math.min(1100, window.innerWidth - 32),
                height: Math.min(720, window.innerHeight - 80),
            }}
            minWidth={420}
            minHeight={320}
            bounds="window"
            style={{zIndex: 60}}
            dragHandleClassName="viewer-panel-drag-handle"
        >
            {body}
        </Rnd>
    );
};

export default InViewerPanelHost;
