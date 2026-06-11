import React from "react";

// Maximize / restore toggle for the storage panel. ``expanded``
// switches between outward arrows (grow to modal) and inward arrows
// (shrink back to the compact popover) so one button reads correctly
// in both states.
const ExpandIcon = ({expanded, ...props}: {expanded?: boolean} & React.SVGProps<SVGSVGElement>) => (
    <svg
        width="14px"
        height="14px"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        {...props}
    >
        {expanded ? (
            <>
                <polyline points="10 3 10 10 3 10"/>
                <polyline points="14 21 14 14 21 14"/>
            </>
        ) : (
            <>
                <polyline points="14 3 21 3 21 10"/>
                <polyline points="10 21 3 21 3 14"/>
                <line x1="21" y1="3" x2="14" y2="10"/>
                <line x1="3" y1="21" x2="10" y2="14"/>
            </>
        )}
    </svg>
);

export default ExpandIcon;
