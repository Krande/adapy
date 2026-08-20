import React, {Suspense, lazy} from "react";
import {createPortal} from "react-dom";
import {Icon} from "@/components/icons";
import {IconButton} from "@/components/ui";
import {Z} from "./zIndex";

const AnimationControls = lazy(() =>
    import("@/components/simulation/SimulationControls").then((m) => ({default: m.AnimationControls})),
);

// The result field and its display knobs, hung off the Results toolbar.
//
// These were the Simulation panel. Play, stop, the legend and the data table left it for
// the toolbar a while ago; what stayed was everything that picks a VALUE — which field,
// which component, which step, how much to exaggerate the deflection, which colormap.
// That left a permanently docked panel holding one column of dropdowns, occupying a
// quarter of the window for controls you touch and then leave alone.
//
// A popover is the right shape for that: the same controls, on screen while you are
// setting them and out of the way afterwards, with the 3D getting the space back.
//
// Portalled to <body> because the toolbar sits in a grid item whose z-index creates a
// stacking context — a popover positioned inside it draws UNDER the docks. Same trap the
// marking menu and the panels dropdown both hit.

export default function ResultsControls() {
    const [open, setOpen] = React.useState(false);
    const btnRef = React.useRef<HTMLButtonElement | null>(null);
    const popRef = React.useRef<HTMLDivElement | null>(null);
    const [rect, setRect] = React.useState<DOMRect | null>(null);

    React.useEffect(() => {
        if (!open) return;
        setRect(btnRef.current?.getBoundingClientRect() ?? null);
        const onDown = (e: PointerEvent) => {
            const t = e.target as Node;
            if (popRef.current?.contains(t) || btnRef.current?.contains(t)) return;
            setOpen(false);
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") setOpen(false);
        };
        // Capture phase: a click inside the 3D canvas is handled by the picker and never
        // bubbles, so a bubble-phase listener would leave the popover open over a scene
        // the user has already moved on from.
        window.addEventListener("pointerdown", onDown, true);
        window.addEventListener("keydown", onKey);
        return () => {
            window.removeEventListener("pointerdown", onDown, true);
            window.removeEventListener("keydown", onKey);
        };
    }, [open]);

    return (
        <>
            <IconButton
                ref={btnRef}
                size="sm"
                tooltip="Field, step and display options"
                icon={<Icon name="settings" size="sm" />}
                pressed={open}
                onClick={() => setOpen((v) => !v)}
                aria-haspopup="dialog"
                aria-expanded={open}
            />
            {open &&
                rect &&
                createPortal(
                    <div
                        ref={popRef}
                        role="dialog"
                        aria-label="Result display options"
                        style={{
                            position: "fixed",
                            top: rect.bottom + 4,
                            // Clamp to the viewport: the button can sit far right in a
                            // long strip, and a popover that opens off-screen is the same
                            // as one that did not open.
                            left: Math.max(8, Math.min(rect.left, window.innerWidth - 388)),
                            width: 380,
                            zIndex: Z.contextMenu,
                        }}
                        // Opaque base, tinted layer on top. The panel surfaces carry alpha
                        // in the glass presets, and CSS cannot flatten that — a single
                        // bg-surface-1 popover let the panel underneath show through, so
                        // the Storage header read straight across the controls. Only
                        // surface-0 is opaque by definition.
                        className="rounded-md border border-edge bg-surface-0 shadow-lg"
                    >
                        <div className="rounded-md bg-surface-1 p-2">
                            <Suspense fallback={<p className="p-2 text-xs text-content-muted">Loading…</p>}>
                                <AnimationControls />
                            </Suspense>
                        </div>
                    </div>,
                    document.body,
                )}
        </>
    );
}
