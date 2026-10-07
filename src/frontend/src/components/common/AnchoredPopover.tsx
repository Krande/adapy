// Portal-anchored popover for arbitrary content (forms, notices) next to a
// button. The positioning / dismiss core of PositionedMenu, for content that is
// not a list of menu items.
//
// Why a portal: a popover rendered inline is clipped by any overflow:hidden /
// scrolling ancestor -- the Simulation drawer is one, and the animation-export
// options were cut off at the panel's top edge. document.body is the only
// stacking context that always works.
//
// Placement: left-aligned to the anchor, BELOW it unless that would leave the
// viewport, then above; both edges clamped into the viewport. Re-placed on
// resize and on any ancestor scroll. Dismissed by a pointer-down outside (the
// anchor excepted, so its own toggle keeps working) and by Escape.

import React, {useLayoutEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";

export interface AnchoredPopoverProps {
    /** The element the popover hangs off (usually the button that toggles it). */
    anchorRef: React.RefObject<HTMLElement | null>;
    onClose: () => void;
    children: React.ReactNode;
    className?: string;
    role?: string;
    ariaLabel?: string;
}

const MARGIN = 8;
const GAP = 4;

export const AnchoredPopover: React.FC<AnchoredPopoverProps> = ({
    anchorRef,
    onClose,
    children,
    className = "",
    role = "dialog",
    ariaLabel,
}) => {
    const ref = useRef<HTMLDivElement>(null);
    const [style, setStyle] = useState<React.CSSProperties>({visibility: "hidden", top: 0, left: 0});

    useLayoutEffect(() => {
        const place = () => {
            const rect = anchorRef.current?.getBoundingClientRect();
            const el = ref.current;
            if (!rect || !el) return;
            const w = el.offsetWidth;
            const h = el.offsetHeight;
            const left = Math.max(MARGIN, Math.min(rect.left, window.innerWidth - w - MARGIN));
            let top = rect.bottom + GAP;
            if (top + h > window.innerHeight - MARGIN) top = rect.top - h - GAP;
            top = Math.max(MARGIN, Math.min(top, window.innerHeight - h - MARGIN));
            setStyle({top, left});
        };
        place();
        const onPointerDown = (e: Event) => {
            const target = e.target as Node | null;
            if (!target) return;
            if (ref.current?.contains(target)) return;
            if (anchorRef.current?.contains(target)) return;
            onClose();
        };
        const onKey = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        document.addEventListener("mousedown", onPointerDown);
        // touchstart: iOS Safari doesn't fire mousedown for taps outside portaled content.
        document.addEventListener("touchstart", onPointerDown);
        document.addEventListener("keydown", onKey);
        window.addEventListener("resize", place);
        window.addEventListener("scroll", place, true);
        return () => {
            document.removeEventListener("mousedown", onPointerDown);
            document.removeEventListener("touchstart", onPointerDown);
            document.removeEventListener("keydown", onKey);
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
        };
    }, [anchorRef, onClose]);

    return createPortal(
        <div
            ref={ref}
            role={role}
            aria-label={ariaLabel}
            // z-[70]: same layer as PositionedMenu -- clears the floating panel hosts.
            // max-h + scroll so a short (mobile) viewport still reaches every control.
            className={"fixed z-[70] max-h-[85vh] overflow-y-auto overscroll-contain " + className}
            style={style}
        >
            {children}
        </div>,
        document.body,
    );
};

export default AnchoredPopover;
