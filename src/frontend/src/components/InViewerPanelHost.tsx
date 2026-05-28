import React, {Suspense, lazy} from "react";
import {Rnd} from "react-rnd";
import {useViewerPanelStore, ViewerPanel} from "@/state/viewerPanelStore";

// In-viewer modal host for the Admin and Convert panels. Mounting
// them as a draggable Rnd window keeps the 3D model on-screen while
// the operator pokes at admin tabs or kicks off a conversion — no
// context switch, no losing the camera state.
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

const InViewerPanelHost: React.FC = () => {
    const open = useViewerPanelStore((s) => s.open);
    const closePanel = useViewerPanelStore((s) => s.closePanel);

    if (open === null) return null;

    const openInNewTab = () => {
        window.open(PANEL_PATH[open], "_blank", "noopener");
        closePanel();
    };

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
            // Keep the modal above the 3D canvas + below any browser
            // chrome / toasts. The 3D canvas sits under z-0; the
            // bottom-right toast slot is z-50.
            style={{zIndex: 60}}
            dragHandleClassName="viewer-panel-drag-handle"
        >
            <div className="h-full w-full flex flex-col bg-gray-900 border border-gray-700 rounded-sm shadow-2xl overflow-hidden">
                <div
                    className={
                        "viewer-panel-drag-handle flex items-center gap-2 " +
                        "bg-gray-800 text-gray-100 px-3 py-2 cursor-move " +
                        "border-b border-gray-700 shrink-0"
                    }
                >
                    <span className="font-medium text-sm">{PANEL_TITLE[open]}</span>
                    <div className="ml-auto flex items-center gap-1">
                        <button
                            type="button"
                            onClick={openInNewTab}
                            title="Open in dedicated tab"
                            aria-label="Open in dedicated tab"
                            className={
                                "shrink-0 inline-flex items-center justify-center " +
                                "w-7 h-7 rounded-sm border border-gray-600 " +
                                "bg-gray-700/60 text-gray-200 hover:bg-gray-600 " +
                                "hover:border-gray-400 hover:text-white"
                            }
                            onMouseDown={(e) => e.stopPropagation()}
                        >
                            <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
                                {/* arrow-out / external-link glyph */}
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
                            onClick={closePanel}
                            title="Close"
                            aria-label="Close panel"
                            className={
                                "shrink-0 inline-flex items-center justify-center " +
                                "w-7 h-7 rounded-sm border border-gray-600 " +
                                "bg-gray-700/60 text-gray-200 hover:bg-gray-600 " +
                                "hover:border-gray-400 hover:text-white"
                            }
                            onMouseDown={(e) => e.stopPropagation()}
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
                <div className="flex-1 min-h-0 overflow-hidden">
                    <Suspense fallback={
                        <div className="p-4 text-sm text-gray-400">Loading…</div>
                    }>
                        {open === "admin" && <AdminPanel/>}
                        {open === "convert" && <ConvertPage/>}
                    </Suspense>
                </div>
            </div>
        </Rnd>
    );
};

export default InViewerPanelHost;
