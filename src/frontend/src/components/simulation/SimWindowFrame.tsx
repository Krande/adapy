// Presentation shell for the Simulation panel. One set of tabbed controls, three
// presentations:
//
//   * docked   — the default: inline in the menu column (bottom-left), exactly
//                where the panel has always lived. On a narrow screen it becomes
//                a draggable bottom-sheet (shared `useBottomSheet`).
//   * floating — a centered, draggable + resizable dialog over a dim scrim, for
//                when the docked column is too cramped to read a results table.
//                No new deps: pointer-events drive the drag (title bar) and a
//                corner resize handle. On mobile it opens as a full-height sheet.
//   * window   — fills the viewport with no scrim/drag; the follower route mounts
//                the frame in this mode so a new browser window is all controls.
//
// The frame is chrome only — it owns positioning + the header actions and renders
// whatever tabbed body it is handed. Colours come from core's `--ada-*` theme
// vars so it tracks the active panel theme in light + dark.

import React from "react";

import {useBottomSheet} from "@/utils/useBottomSheet";

export type SimFrameMode = "docked" | "floating" | "window";

const CHROME =
    "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
    "text-[var(--ada-panel-text)] shadow-lg";

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
        className="rounded-sm p-1 text-[var(--ada-panel-text)] pointer-fine:hover:bg-[var(--ada-panel-surface)]"
    >
        {children}
    </button>
);

// Docked panel is horizontally resizable and remembers its width. The default is
// wide enough that plugin tabs with tabular content (e.g. results tables) don't
// force horizontal scrolling out of the box; the user can drag the right edge to
// widen further (persisted) or narrow it back.
const DOCKED_WIDTH_KEY = "ada:sim-docked-width";
const DOCKED_DEFAULT = 620;
const DOCKED_MIN = 360;

const SimWindowFrame: React.FC<Props> = ({mode, setMode, onOpenWindow, title = "Simulation", children}) => {
    const {panelRef, isMobile, sheetStyle, grab} = useBottomSheet(() => setMode("docked"));

    const [dockedWidth, setDockedWidth] = React.useState<number>(() => {
        if (typeof window === "undefined") return DOCKED_DEFAULT;
        const saved = parseInt(window.localStorage.getItem(DOCKED_WIDTH_KEY) || "", 10);
        return Number.isFinite(saved) && saved >= DOCKED_MIN ? saved : DOCKED_DEFAULT;
    });
    React.useEffect(() => {
        if (typeof window !== "undefined") window.localStorage.setItem(DOCKED_WIDTH_KEY, String(dockedWidth));
    }, [dockedWidth]);
    const dockResizeRef = React.useRef<{sx: number; ow: number} | null>(null);
    const onDockResizeDown = (e: React.PointerEvent) => {
        e.preventDefault();
        dockResizeRef.current = {sx: e.clientX, ow: dockedWidth};
        e.currentTarget.setPointerCapture(e.pointerId);
    };
    const onDockResizeMove = (e: React.PointerEvent) => {
        const d = dockResizeRef.current;
        if (!d) return;
        const max = typeof window !== "undefined" ? Math.max(DOCKED_MIN, window.innerWidth - 32) : 1400;
        setDockedWidth(Math.min(max, Math.max(DOCKED_MIN, d.ow + (e.clientX - d.sx))));
    };
    const onDockResizeUp = (e: React.PointerEvent) => {
        if (!dockResizeRef.current) return;
        dockResizeRef.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
    };

    // Floating-dialog geometry (desktop only). Centred on first maximize; the
    // title bar drags it and the corner handle resizes it, both in raw pixels so
    // the math stays in one coordinate space (see useBottomSheet's note).
    const [rect, setRect] = React.useState<{x: number; y: number; w: number; h: number} | null>(null);
    const dragRef = React.useRef<{mode: "move" | "resize"; sx: number; sy: number; ox: number; oy: number; ow: number; oh: number} | null>(null);

    React.useEffect(() => {
        if (mode !== "floating" || rect || typeof window === "undefined") return;
        const w = Math.min(560, window.innerWidth - 48);
        const h = Math.min(640, window.innerHeight - 96);
        setRect({x: Math.max(24, (window.innerWidth - w) / 2), y: Math.max(48, (window.innerHeight - h) / 2), w, h});
    }, [mode, rect]);

    const onPointerDown = (kind: "move" | "resize") => (e: React.PointerEvent) => {
        if (!rect) return;
        e.preventDefault();
        dragRef.current = {mode: kind, sx: e.clientX, sy: e.clientY, ox: rect.x, oy: rect.y, ow: rect.w, oh: rect.h};
        e.currentTarget.setPointerCapture(e.pointerId);
    };
    const onPointerMove = (e: React.PointerEvent) => {
        const d = dragRef.current;
        if (!d) return;
        const dx = e.clientX - d.sx;
        const dy = e.clientY - d.sy;
        if (d.mode === "move") {
            setRect({x: Math.max(0, d.ox + dx), y: Math.max(0, d.oy + dy), w: d.ow, h: d.oh});
        } else {
            setRect({x: d.ox, y: d.oy, w: Math.max(280, d.ow + dx), h: Math.max(200, d.oh + dy)});
        }
    };
    const onPointerUp = (e: React.PointerEvent) => {
        if (!dragRef.current) return;
        dragRef.current = null;
        e.currentTarget.releasePointerCapture?.(e.pointerId);
    };

    const header = (
        <div
            className={
                "shrink-0 flex items-center justify-between gap-2 px-2 py-1 border-b border-[var(--ada-panel-border)] " +
                (mode === "floating" && !isMobile ? "cursor-move select-none touch-none" : "")
            }
            {...(mode === "floating" && !isMobile ? {onPointerDown: onPointerDown("move"), onPointerMove, onPointerUp, onPointerCancel: onPointerUp} : {})}
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
            <div className={CHROME + " fixed inset-0 z-40 flex flex-col overflow-hidden"}>
                {header}
                <div className="min-h-0 flex-1 overflow-y-auto p-2">{children}</div>
            </div>
        );
    }

    // ── mobile: bottom sheet for docked AND floating (floating = taller) ──
    if (isMobile) {
        // Height cap comes from useBottomSheet's sheetStyle (visible-viewport
        // pixels), NOT a `vh` class — `vh` tracks the large viewport and would let
        // the sheet's top (and grab handle) leave the screen when the mobile
        // toolbar is shown, making it impossible to drag closed.
        const base = "fixed inset-x-0 bottom-0 z-30 w-full rounded-t-2xl flex flex-col overflow-hidden";
        return (
            <div ref={panelRef} style={sheetStyle} className={CHROME + " " + base}>
                <div
                    className="shrink-0 flex justify-center items-center py-2 cursor-grab active:cursor-grabbing touch-none"
                    role="separator"
                    aria-label="Drag to resize the panel"
                    {...grab}
                >
                    <span className="block w-10 h-1.5 rounded-full bg-surface-3" aria-hidden="true" />
                </div>
                {header}
                <div className="min-h-0 flex-1 overflow-y-auto p-2">{children}</div>
            </div>
        );
    }

    // ── floating: a non-modal, draggable + resizable window ──
    // No scrim: the viewer behind stays fully interactive (rotate / pick / edit)
    // while the floating panel is open. Floating just means "detached window you
    // can move and resize"; use Restore in the header to dock it again.
    if (mode === "floating") {
        return (
            <>
                <div
                    className={CHROME + " fixed z-40 flex flex-col overflow-hidden rounded-md"}
                    style={rect ? {left: rect.x, top: rect.y, width: rect.w, height: rect.h} : undefined}
                    role="dialog"
                    aria-label={title}
                >
                    {header}
                    <div className="min-h-0 flex-1 overflow-y-auto p-2">{children}</div>
                    {/* corner resize handle */}
                    <div
                        className="absolute bottom-0 right-0 w-4 h-4 cursor-se-resize touch-none"
                        onPointerDown={onPointerDown("resize")}
                        onPointerMove={onPointerMove}
                        onPointerUp={onPointerUp}
                        onPointerCancel={onPointerUp}
                        aria-hidden="true"
                    >
                        <svg viewBox="0 0 10 10" className="w-full h-full text-[var(--ada-panel-text-muted,#9ca3af)]" fill="none" stroke="currentColor" strokeWidth="1">
                            <path d="M9 1 1 9M9 5 5 9" />
                        </svg>
                    </div>
                </div>
            </>
        );
    }

    // ── docked: inline in the menu column (default), horizontally resizable ──
    return (
        <div
            className={CHROME + " relative rounded-md flex flex-col overflow-hidden"}
            style={{width: dockedWidth, minWidth: DOCKED_MIN, maxWidth: "calc(100vw - 32px)"}}
        >
            {header}
            <div className="min-h-0 overflow-auto p-2 max-h-[70vh]">{children}</div>
            {/* Right-edge handle — drag to resize the panel horizontally. */}
            <div
                className="absolute top-0 right-0 h-full w-1.5 cursor-ew-resize touch-none pointer-fine:hover:bg-accent-subtle"
                onPointerDown={onDockResizeDown}
                onPointerMove={onDockResizeMove}
                onPointerUp={onDockResizeUp}
                onPointerCancel={onDockResizeUp}
                role="separator"
                aria-orientation="vertical"
                aria-label="Resize simulation panel width"
            />
        </div>
    );
};

export default SimWindowFrame;
