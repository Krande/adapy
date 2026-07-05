// Portal-anchored popup menu — the positioning/dismiss core shared by
// the per-row kebab menu (anchored to its button rect) and the storage
// panel's right-click context menu (anchored to the cursor point).
//
// Why a portal instead of inline rendering: the menu has to clear
// the row's overflow:hidden / z-index so it can spill out of
// scrollable lists without being clipped. document.body is the only
// stacking context that always works.
//
// Click-outside dismiss runs via a mousedown listener on document.
// Rect anchors re-place on resize/scroll so the menu tracks the button
// position without re-opening; point anchors instead close on scroll —
// there's no element to track, and a context menu drifting away from
// the row it was opened on reads as a bug.

import React, {useLayoutEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";

export interface KebabMenuItem {
    /** Stable React key + identity. Disabled / hidden state is per
     *  item, not folded into the key. */
    key: string;
    label: string;
    /** Optional leading icon — a rendered SVG element, not a
     *  component. Pass ``<KebabIcon class="h-3.5 w-3.5"/>`` etc.
     *  Keeps the menu icon-shape decoupled from the icon-library
     *  choice (adapy uses inline SVGs; shelf uses lucide-react). */
    icon?: React.ReactNode;
    onClick: () => void;
    disabled?: boolean;
    /** Renders the label in red. Use sparingly — Delete-like actions
     *  only, so the destructive colour stays meaningful. */
    destructive?: boolean;
    /** When true, draws a horizontal divider above this item. Mirrors
     *  shelf's pattern where the destructive section is visually
     *  separated from the rest. */
    separatorBefore?: boolean;
    /** Optional tooltip on the menu item itself — useful for
     *  surfacing why an action is disabled. */
    title?: string;
}

export type MenuAnchor =
    | {kind: "rect"; getRect: () => DOMRect | undefined}
    | {kind: "point"; x: number; y: number};

interface PositionedMenuProps {
    items: KebabMenuItem[];
    anchor: MenuAnchor;
    onClose: () => void;
    /** Optional non-interactive heading rendered at the top of the menu,
     *  above the items (e.g. the row's full storage path). */
    header?: React.ReactNode;
    /** Elements whose clicks should NOT dismiss the menu (e.g. the
     *  kebab button that toggles it — toggling handles its own close). */
    ignoreOutsideRef?: React.RefObject<HTMLElement | null>;
}

export const PositionedMenu: React.FC<PositionedMenuProps> = ({
    items,
    anchor,
    onClose,
    header,
    ignoreOutsideRef,
}) => {
    const menuRef = useRef<HTMLDivElement>(null);
    const [style, setStyle] = useState<React.CSSProperties>(() =>
        anchor.kind === "point"
            ? {top: anchor.y, left: anchor.x, visibility: "hidden"}
            : {visibility: "hidden"},
    );

    useLayoutEffect(() => {
        const place = () => {
            if (anchor.kind === "rect") {
                const rect = anchor.getRect();
                if (!rect) return;
                setStyle({
                    top: rect.bottom + 4,
                    right: window.innerWidth - rect.right,
                });
                return;
            }
            // Point anchor: clamp to the viewport so a right-click near
            // the bottom/right edge flips the menu inward instead of
            // spilling off-screen.
            const menu = menuRef.current;
            const w = menu?.offsetWidth ?? 200;
            const h = menu?.offsetHeight ?? 160;
            const left = Math.min(anchor.x, window.innerWidth - w - 8);
            const top = Math.min(anchor.y, window.innerHeight - h - 8);
            setStyle({top: Math.max(8, top), left: Math.max(8, left)});
        };
        place();
        const onClickOutside = (e: Event) => {
            const target = e.target as Node | null;
            if (!target) return;
            if (menuRef.current?.contains(target)) return;
            if (ignoreOutsideRef?.current?.contains(target)) return;
            onClose();
        };
        const onScroll = anchor.kind === "rect" ? place : onClose;
        // mousedown not click so the menu dismisses before any
        // background click handler (e.g. the row's onClick) fires.
        document.addEventListener("mousedown", onClickOutside);
        // touchstart on iOS — Safari doesn't fire mousedown for taps
        // outside the document hierarchy on portaled content.
        document.addEventListener("touchstart", onClickOutside);
        window.addEventListener("resize", place);
        // capture:true so rect menus re-place (and point menus close)
        // when a scrollable ancestor scrolls, not only the window.
        window.addEventListener("scroll", onScroll, true);
        return () => {
            document.removeEventListener("mousedown", onClickOutside);
            document.removeEventListener("touchstart", onClickOutside);
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", onScroll, true);
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [anchor.kind, anchor.kind === "point" ? anchor.x : 0, anchor.kind === "point" ? anchor.y : 0]);

    const run = (fn: () => void) => (e: React.MouseEvent) => {
        e.stopPropagation();
        onClose();
        fn();
    };

    return createPortal(
        <div
            ref={menuRef}
            role="menu"
            // z-[70]: body-portaled, so it must clear the floating admin
            // panel host (fixed z-[60]) — the corpus tab's kebab/context
            // menus open from inside it.
            className="fixed z-[70] min-w-[180px] rounded-sm border border-gray-700 bg-gray-800 shadow-lg text-gray-100"
            style={style}
            onClick={(e) => e.stopPropagation()}
            onContextMenu={(e) => e.preventDefault()}
        >
            {header && (
                <div className="px-3 py-1.5 text-[11px] text-gray-400 border-b border-gray-700 break-all">
                    {header}
                </div>
            )}
            {items.map((item) => (
                <React.Fragment key={item.key}>
                    {item.separatorBefore && (
                        <div className="my-1 border-t border-gray-700"/>
                    )}
                    <button
                        type="button"
                        role="menuitem"
                        disabled={item.disabled}
                        onClick={run(item.onClick)}
                        title={item.title}
                        className={
                            "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs " +
                            "hover:bg-gray-700 disabled:opacity-40 disabled:hover:bg-transparent " +
                            (item.destructive ? "text-red-400 hover:text-red-300 " : "")
                        }
                    >
                        {item.icon && (
                            <span className="inline-flex h-3.5 w-3.5 items-center justify-center shrink-0">
                                {item.icon}
                            </span>
                        )}
                        <span className="truncate">{item.label}</span>
                    </button>
                </React.Fragment>
            ))}
        </div>,
        document.body,
    );
};

export default PositionedMenu;
