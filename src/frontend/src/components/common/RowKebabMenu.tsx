// Per-row kebab menu — three-dot button that opens a portal-anchored
// menu below it. Borrowed pattern from shelf's CollectionRail; ported
// to adapy's inline-SVG icon convention (no lucide-react dep) and
// dark theme (bg-gray-800 / border-gray-700).
//
// Why a portal instead of inline rendering: the menu has to clear
// the row's overflow:hidden / z-index so it can spill out of
// scrollable lists without being clipped. document.body is the only
// stacking context that always works.
//
// Click-outside dismiss runs via a mousedown listener on document.
// Resize + scroll re-place the menu so it tracks the button position
// without re-opening — important when the row scrolls under a sticky
// header.

import React, {useEffect, useRef, useState} from "react";
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

interface RowKebabMenuProps {
    /** Accessible button label. Caller supplies context — "More
     *  actions for foo.ifc" or similar. */
    ariaLabel: string;
    items: KebabMenuItem[];
    /** When true, the kebab button itself is disabled (e.g. row is
     *  busy with another action). The menu can't open. */
    disabled?: boolean;
    /** Optional extra classes on the button. Default is a small
     *  square hit area that fits inside table rows; mobile callers
     *  can pass ``p-2`` to bump the tap target. */
    buttonClassName?: string;
    /** Optional non-interactive heading rendered at the top of the menu,
     *  above the items (e.g. the row's full storage path). */
    header?: React.ReactNode;
}

export const RowKebabMenu: React.FC<RowKebabMenuProps> = ({
    ariaLabel,
    items,
    header,
    disabled,
    buttonClassName,
}) => {
    const [open, setOpen] = useState(false);
    const [pos, setPos] = useState<{top: number; right: number} | null>(null);
    const buttonRef = useRef<HTMLButtonElement>(null);
    const menuRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!open) return;
        const place = () => {
            const rect = buttonRef.current?.getBoundingClientRect();
            if (!rect) return;
            setPos({
                top: rect.bottom + 4,
                right: window.innerWidth - rect.right,
            });
        };
        place();
        const onClickOutside = (e: Event) => {
            const target = e.target as Node | null;
            if (!target) return;
            if (buttonRef.current?.contains(target)) return;
            if (menuRef.current?.contains(target)) return;
            setOpen(false);
        };
        // mousedown not click so the menu dismisses before any
        // background click handler (e.g. the row's onClick) fires.
        document.addEventListener("mousedown", onClickOutside);
        // touchstart on iOS — Safari doesn't fire mousedown for taps
        // outside the document hierarchy on portaled content.
        document.addEventListener("touchstart", onClickOutside);
        window.addEventListener("resize", place);
        // capture:true so the menu re-places when a scrollable
        // ancestor scrolls, not only the window.
        window.addEventListener("scroll", place, true);
        return () => {
            document.removeEventListener("mousedown", onClickOutside);
            document.removeEventListener("touchstart", onClickOutside);
            window.removeEventListener("resize", place);
            window.removeEventListener("scroll", place, true);
        };
    }, [open]);

    const run = (fn: () => void) => (e: React.MouseEvent) => {
        e.stopPropagation();
        setOpen(false);
        fn();
    };

    return (
        <>
            <button
                ref={buttonRef}
                type="button"
                disabled={disabled}
                onClick={(e) => {
                    e.stopPropagation();
                    if (disabled) return;
                    setOpen((v) => !v);
                }}
                aria-label={ariaLabel}
                aria-haspopup="menu"
                aria-expanded={open}
                className={
                    "inline-flex items-center justify-center rounded-sm text-gray-300 " +
                    "hover:bg-gray-700 active:bg-gray-600 disabled:opacity-40 " +
                    "focus:outline-hidden focus:ring-2 focus:ring-blue-400 " +
                    (buttonClassName ?? "h-7 w-7 sm:h-6 sm:w-6")
                }
                title={disabled ? "busy…" : ariaLabel}
            >
                <KebabDotsIcon/>
            </button>
            {open && pos && createPortal(
                <div
                    ref={menuRef}
                    role="menu"
                    className="fixed z-50 min-w-[180px] rounded-sm border border-gray-700 bg-gray-800 shadow-lg text-gray-100"
                    style={{top: pos.top, right: pos.right}}
                    onClick={(e) => e.stopPropagation()}
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
            )}
        </>
    );
};

const KebabDotsIcon: React.FC = () => (
    <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="currentColor"
        aria-hidden="true"
    >
        <circle cx="5" cy="12" r="1.8"/>
        <circle cx="12" cy="12" r="1.8"/>
        <circle cx="19" cy="12" r="1.8"/>
    </svg>
);

export default RowKebabMenu;
