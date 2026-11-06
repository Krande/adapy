import React from "react";
import {ViewerPanel} from "@/state/viewerPanelStore";
import {FloatingWindow} from "@/components/ui/FloatingWindow";
import {CloseGlyph, ExternalGlyph, WindowFrame, WindowHeaderActions, WindowHeaderButton} from "@/components/ui/PanelFrame";

// The in-viewer window of the Admin and Convert panels, drawn by the classic
// preset's window layer (`layout/presets/classic.tsx`, `ViewerWindowChrome`).
// Mounting them as a draggable window keeps the 3D model on-screen while the
// operator pokes at admin tabs or kicks off a conversion -- no context switch,
// no losing the camera state.
//
// Layout mode is responsive (both halves are `FloatingWindow`'s):
// * Desktop (>=768px): draggable + resizable window over the viewer.
// * Mobile (<768px): fixed full-screen overlay. Drag-resizing a ~360px-wide
//   window on a touch screen is hostile; the standard mobile pattern is "panel
//   takes over." The drag handle goes away, but the same header buttons (close,
//   open in new tab) stay in place.
//
// The dedicated path-mounted ``/admin`` and ``/convert`` routes remain available
// for direct URL navigation and for the "open in a dedicated tab" button in the
// window header (the arrow-out icon below).

export const VIEWER_PANEL_TITLE: Record<ViewerPanel, string> = {
    admin: "Admin panel",
    convert: "Convert files",
};

const PANEL_PATH: Partial<Record<ViewerPanel, string>> = {
    admin: "/admin",
    convert: "/convert",
};

const DRAG_HANDLE = "viewer-panel-drag-handle";

/**
 * "Open in a dedicated tab" for a viewer panel, or null when it has no route of
 * its own. Carries the embedded admin tab into the dedicated route so the new
 * tab lands where the floating panel was (the full-page admin reads it from the
 * hash), then closes the floating panel.
 */
export function viewerPanelExternal(panel: ViewerPanel, adminTab: string | null, close: () => void): (() => void) | null {
    const path = PANEL_PATH[panel];
    if (!path) return null;
    return () => {
        const hash = panel === "admin" && adminTab ? `#${adminTab}` : "";
        window.open(path + hash, "_blank", "noopener");
        close();
    };
}

/**
 * The in-viewer window: centred near the top at 1100x720 (capped to the
 * viewport), above the canvas and the tree but under the toast slot, a
 * full-screen takeover on narrow screens. `offset` steps it down and right, for a
 * second window beside the first; `layer` stacks it over the other.
 */
export const ViewerWindow: React.FC<{
    title: string;
    onExternal: (() => void) | null;
    onClose: () => void;
    offset?: number;
    /** Stacking above the other in-viewer window (0: the base layer). */
    layer?: number;
    onActivate?: () => void;
    children: React.ReactNode;
}> = ({title, onExternal, onClose, offset = 0, layer = 0, onActivate, children}) => (
    <FloatingWindow
        dragHandleClassName={DRAG_HANDLE}
        // z-60 keeps it above the canvas (z-0) and below the toast slot (z-50,
        // intentionally inverted on mobile so the panel can show the same
        // toasts the user already saw in the drawer).
        zIndex={60 + layer}
        zClassName="z-[60]"
        onActivate={onActivate}
        minSize={{width: 420, height: 320}}
        initialRect={() => ({
            // Centered-ish over the viewport. Big enough that the admin tabs +
            // audit-runs grid don't feel claustrophobic; the user can
            // drag-resize as they like.
            x: Math.max(0, (window.innerWidth - 1100) / 2) + offset,
            y: 60 + offset,
            width: Math.min(1100, window.innerWidth - 32),
            height: Math.min(720, window.innerHeight - 80),
        })}
        fullScreenWhenNarrow
    >
        {({fullScreen}) => (
            <WindowFrame
                title={title}
                dragHandleClassName={fullScreen ? null : DRAG_HANDLE}
                actions={
                    <WindowHeaderActions>
                        {onExternal && (
                            <WindowHeaderButton onClick={onExternal} title="Open in dedicated tab" ariaLabel="Open in dedicated tab">
                                <ExternalGlyph/>
                            </WindowHeaderButton>
                        )}
                        <WindowHeaderButton onClick={onClose} title="Close" ariaLabel="Close panel">
                            <CloseGlyph/>
                        </WindowHeaderButton>
                    </WindowHeaderActions>
                }
            >
                {children}
            </WindowFrame>
        )}
    </FloatingWindow>
);
