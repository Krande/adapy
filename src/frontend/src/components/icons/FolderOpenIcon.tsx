import React from "react";

// Open folder — used by the storage browser when its FolderRow is
// expanded. Pairs with FolderClosedIcon. ``currentColor`` so the
// parent's text colour wins, matching TreeViewIcon / GroupIcon.
const FolderOpenIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg
        width="16px"
        height="16px"
        viewBox="0 0 24 24"
        fill="currentColor"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        {...props}
    >
        <path d="M10 4H2v16h2.5l3-10H22V6H12l-2-2zm-3.5 8L4 20h16l3-8H6.5z"/>
    </svg>
);

export default FolderOpenIcon;
