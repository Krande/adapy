// Per-row kebab menu — three-dot button that opens a portal-anchored
// menu below it. Borrowed pattern from shelf's CollectionRail; ported
// to adapy's inline-SVG icon convention (no lucide-react dep) and
// dark theme (bg-gray-800 / border-gray-700).
//
// The positioning/dismiss machinery lives in PositionedMenu (shared
// with the storage panel's right-click context menu); this component
// is just the toggle button + a rect anchor.

import React, {useRef, useState} from "react";
import PositionedMenu, {KebabMenuItem} from "./PositionedMenu";

export type {KebabMenuItem};

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
    const buttonRef = useRef<HTMLButtonElement>(null);

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
            {open && (
                <PositionedMenu
                    items={items}
                    header={header}
                    onClose={() => setOpen(false)}
                    ignoreOutsideRef={buttonRef}
                    anchor={{
                        kind: "rect",
                        getRect: () => buttonRef.current?.getBoundingClientRect(),
                    }}
                />
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
