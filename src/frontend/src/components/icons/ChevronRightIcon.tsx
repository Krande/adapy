import React from "react";

// Right-pointing chevron used by the storage browser's FolderRow as
// the expand affordance. Rotate 90° via Tailwind's rotate-90 utility
// to get a "down" chevron when the folder is expanded — one icon,
// two states, no second file.
//
// ``currentColor`` so a parent .text-blue-400 colours it without
// per-instance overrides, same convention as the other icons here.
const ChevronRightIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg
        width="14px"
        height="14px"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        {...props}
    >
        <polyline points="9 6 15 12 9 18"/>
    </svg>
);

export default ChevronRightIcon;
