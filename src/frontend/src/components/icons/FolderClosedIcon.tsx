import React from "react";

// Closed folder. ``currentColor`` so a parent .text-blue-300 / .text-white
// flips the colour without the icon caring — same idiom as
// TreeViewIcon, GroupIcon, etc.
const FolderClosedIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg
        width="16px"
        height="16px"
        viewBox="0 0 24 24"
        fill="currentColor"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        {...props}
    >
        <path d="M10 4H2v16h20V6H12l-2-2z"/>
    </svg>
);

export default FolderClosedIcon;
