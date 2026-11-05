// Presentation shell for the Simulation panel. One set of tabbed controls, three
// presentations:
//
//   * docked   — the default: inline in the menu column (bottom-left), exactly
//                where the panel has always lived. On a narrow screen it becomes
//                a draggable bottom-sheet (shared `useBottomSheet`).
//   * floating — a centered, draggable + resizable dialog, for when the docked
//                column is too cramped to read a results table. Pointer events
//                drive the drag (title bar) and a corner resize handle, through
//                the shared `usePointerDrag` and the pure rect helpers of
//                `@/assets/floatingWindow`. On mobile it opens as a full-height sheet.
//   * window   — fills the viewport with no scrim/drag; the follower route mounts
//                the frame in this mode so a new browser window is all controls.
//
// The frame is chrome only — it owns positioning + the header actions and renders
// whatever tabbed body it is handed (`SimulationBody`). Colours come from core's
// `--ada-*` theme vars (the shared panel surface) so it tracks the active panel
// theme in light + dark.

import React from "react";

import {centredRect, moveRect, resizeRect, type WindowRect} from "@/assets/floatingWindow";
import {usePointerDrag} from "@/components/ui/FloatingWindow";
import {PanelCard} from "@/components/ui/PanelFrame";
import {SheetHandle} from "@/components/ui/SheetHandle";
import {useBottomSheet} from "@/utils/useBottomSheet";

export type SimFrameMode = "docked" | "floating" | "window";

interface Props {
    mode: SimFrameMode;
    setMode: (m: SimFrameMode) => void;
    /** Opens the same content in a separate browser window (new-window action). */
    onOpenWindow?: () => void;
    title?: string;
    children: React.ReactNode;
}

const MaximizeIcon: React.FC = () => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4" aria-hidden="true">
        <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" />
    </svg>
);

const RestoreIcon: React.FC = () => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4" aria-hidden="true">
        <path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" />
    </svg>
);

const NewWindowIcon: React.FC = () => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4" aria-hidden="true">
        <path d="M15 3h6v6M10 14 21 3M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
    </svg>
);

const HeaderBtn: React.FC<{onClick: () => void; title: string; children: React.ReactNode}> = ({onClick, title, children}) => (
    <button
        type="button"
        onClick={onClick}
        title={title}
        aria-label={title}
        className="rounded-sm p-1 text-[var(--ada-panel-text)] hover:bg-[var(--ada-panel-surface)]"
    >
        {children}
    </button>
);

// Docked panel: sized to its content within [DOCKED_MIN, DOCKED_AUTO_MAX] until the
// user drags the right edge, which sets (and persists) an explicit width. A
// double-click on the edge returns it to content size. The key was bumped when the
// fixed default was dropped, so a width saved under it does not come back.
const DOCKED_WIDTH_KEY = "ada:sim-docked-width-v2";
export const DOCKED_MIN = 360;
export const DOCKED_AUTO_MAX = 520;

/** The floating dialog's first size and its margins from the viewport's edges. */
const FLOAT_PREFERRED = {width: 560, height: 640};
const FLOAT_MARGIN = {x: 24, y: 48, top: 48};
/** The smallest the corner handle resizes the floating dialog to. */
const FLOAT_MIN = {width: 280, height: 200};

/** The docked panel's width style: content-sized and capped when `width` is null,
 *  else the user's width. Bounded by the window and the container either way. */
export function dockedFrameStyle(width: number | null): React.CSSProperties {
    return {
        width: width ?? "fit-content",
        minWidth: `min(${DOCKED_MIN}px, 100%)`,
        maxWidth: width === null
            ? `min(${DOCKED_AUTO_MAX}px, calc(100vw - 32px), 100%)`
            : "min(calc(100vw - 32px), 100%)",
    };
}

const SimWindowFrame: React.FC<Props> = ({mode, setMode, onOpenWindow, title = "Simulation", children}) => {
    const {panelRef, isMobile, sheetStyle, grab} = useBottomSheet(() => setMode("docked"));

    const [dockedWidth, setDockedWidth] = React.useState<number | null>(() => {
        if (typeof window === "undefined") return null;
        const saved = parseInt(window.localStorage.getItem(DOCKED_WIDTH_KEY) || "", 10);
        return Number.isFinite(saved) && saved >= DOCKED_MIN ? saved : null;
    });
    React.useEffect(() => {
        if (typeof window === "undefined") return;
        if (dockedWidth === null) window.localStorage.removeItem(DOCKED_WIDTH_KEY);
        else window.localStorage.setItem(DOCKED_WIDTH_KEY, String(dockedWidth));
    }, [dockedWidth]);
    const dockedRef = React.useRef<HTMLDivElement>(null);
    const dockResize = usePointerDrag<number>((ow, dx) => {
        const max = typeof window !== "undefined" ? Math.max(DOCKED_MIN, window.innerWidth - 32) : 1400;
        setDockedWidth(Math.min(max, Math.max(DOCKED_MIN, ow + dx)));
    });
    // From the width on screen: a content-sized panel has no number of its own yet.
    const onDockResizeDown = (e: React.PointerEvent) =>
        dockResize.begin(e, dockedWidth ?? dockedRef.current?.getBoundingClientRect().width ?? DOCKED_MIN);

    // Floating-dialog geometry (desktop only). Centred on first maximize; the
    // title bar drags it and the corner handle resizes it, both in raw pixels so
    // the math stays in one coordinate space (see useBottomSheet's note).
    const [rect, setRect] = React.useState<WindowRect | null>(null);

    React.useEffect(() => {
        if (mode !== "floating" || rect || typeof window === "undefined") return;
        setRect(centredRect({width: window.innerWidth, height: window.innerHeight}, FLOAT_PREFERRED, FLOAT_MARGIN));
    }, [mode, rect]);

    const move = usePointerDrag<WindowRect>((start, dx, dy) => setRect(moveRect(start, dx, dy)));
    const resize = usePointerDrag<WindowRect>((start, dx, dy) => setRect(resizeRect(start, dx, dy, FLOAT_MIN)));
    const onMoveDown = (e: React.PointerEvent) => {
        if (rect) move.begin(e, rect);
    };
    const onResizeDown = (e: React.PointerEvent) => {
        if (rect) resize.begin(e, rect);
    };

    const header = (
        <div
            className={
                "shrink-0 flex items-center justify-between gap-2 px-2 py-1 border-b border-[var(--ada-panel-border)] " +
                (mode === "floating" && !isMobile ? "cursor-move select-none touch-none" : "")
            }
            {...(mode === "floating" && !isMobile
                ? {onPointerDown: onMoveDown, onPointerMove: move.onPointerMove, onPointerUp: move.onPointerUp, onPointerCancel: move.onPointerCancel}
                : {})}
        >
            <span className="font-bold text-sm">{title}</span>
            {/* Stop pointerdown here from reaching the header's drag handler — in
                floating mode the header captures the pointer on pointerdown, which
                otherwise swallows the button clicks (Restore / new-window). */}
            <div className="flex items-center gap-0.5" onPointerDown={(e) => e.stopPropagation()}>
                {mode === "docked" && (
                    <HeaderBtn onClick={() => setMode("floating")} title="Floating window">
                        <MaximizeIcon />
                    </HeaderBtn>
                )}
                {mode === "floating" && (
                    <HeaderBtn onClick={() => setMode("docked")} title="Restore">
                        <RestoreIcon />
                    </HeaderBtn>
                )}
                {mode !== "window" && onOpenWindow && (
                    <HeaderBtn onClick={onOpenWindow} title="Open in new window">
                        <NewWindowIcon />
                    </HeaderBtn>
                )}
            </div>
        </div>
    );

    // ── window: fill the viewport (follower route) ──
    if (mode === "window") {
        return (
            <PanelCard variant="surface" className="fixed inset-0 z-40 flex flex-col overflow-hidden">
                {header}
                <div className="min-h-0 flex-1 overflow-y-auto p-2">{children}</div>
            </PanelCard>
        );
    }

    // ── mobile: bottom sheet for docked AND floating (floating = taller) ──
    if (isMobile) {
        // Height cap comes from useBottomSheet's sheetStyle (visible-viewport
        // pixels), NOT a `vh` class — `vh` tracks the large viewport and would let
        // the sheet's top (and grab handle) leave the screen when the mobile
        // toolbar is shown, making it impossible to drag closed.
        return (
            <PanelCard
                ref={panelRef}
                style={sheetStyle}
                variant="surface"
                className="fixed inset-x-0 bottom-0 z-30 w-full rounded-t-2xl flex flex-col overflow-hidden"
            >
                <SheetHandle grab={grab} />
                {header}
                <div className="min-h-0 flex-1 overflow-y-auto p-2">{children}</div>
            </PanelCard>
        );
    }

    // ── floating: a non-modal, draggable + resizable window ──
    // No scrim: the viewer behind stays fully interactive (rotate / pick / edit)
    // while the floating panel is open. Floating just means "detached window you
    // can move and resize"; use Restore in the header to dock it again.
    if (mode === "floating") {
        return (
            <>
                <PanelCard
                    variant="surface"
                    className="fixed z-40 flex flex-col overflow-hidden rounded-md"
                    style={rect ? {left: rect.x, top: rect.y, width: rect.width, height: rect.height} : undefined}
                    role="dialog"
                    aria-label={title}
                >
                    {header}
                    <div className="min-h-0 flex-1 overflow-y-auto p-2">{children}</div>
                    {/* corner resize handle */}
                    <div
                        className="absolute bottom-0 right-0 w-4 h-4 cursor-se-resize touch-none"
                        onPointerDown={onResizeDown}
                        onPointerMove={resize.onPointerMove}
                        onPointerUp={resize.onPointerUp}
                        onPointerCancel={resize.onPointerCancel}
                        aria-hidden="true"
                    >
                        <svg viewBox="0 0 10 10" className="w-full h-full text-[var(--ada-panel-text-muted,#9ca3af)]" fill="none" stroke="currentColor" strokeWidth="1">
                            <path d="M9 1 1 9M9 5 5 9" />
                        </svg>
                    </div>
                </PanelCard>
            </>
        );
    }

    // ── docked: inline in the menu column (default), horizontally resizable ──
    // Bounded by its container as well as by the window: in a small embedded
    // viewer (a 3D figure in a document) the window is wide but the viewer is
    // not, and a panel sized to the window ran off the figure's edge. The
    // embed sets ``--ada-sim-max-h`` to what fits under its toolbar; the
    // standalone app leaves it unset and keeps 70vh.
    return (
        <PanelCard
            ref={dockedRef}
            variant="surface"
            className="relative rounded-md flex flex-col overflow-hidden"
            style={dockedFrameStyle(dockedWidth)}
        >
            {header}
            <div className="min-h-0 overflow-auto p-2" style={{maxHeight: "var(--ada-sim-max-h, 70vh)"}}>
                {children}
            </div>
            {/* Right-edge handle — drag to resize the panel horizontally, double-click to fit the content. */}
            <div
                className="absolute top-0 right-0 h-full w-1.5 cursor-ew-resize touch-none hover:bg-blue-400/40"
                onPointerDown={onDockResizeDown}
                onPointerMove={dockResize.onPointerMove}
                onPointerUp={dockResize.onPointerUp}
                onPointerCancel={dockResize.onPointerCancel}
                onDoubleClick={() => setDockedWidth(null)}
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize simulation panel width"
                title="Drag to resize; double-click to fit the content"
            />
        </PanelCard>
    );
};

export default SimWindowFrame;
