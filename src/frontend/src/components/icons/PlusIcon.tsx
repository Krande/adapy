import React from "react";

// Plus sign for the storage panel's "add" menu (upload files / new
// folder). ``currentColor`` so a parent text-* class colours it, same
// convention as the other icons here.
const PlusIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg
        width="16px"
        height="16px"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
        {...props}
    >
        <line x1="12" y1="5" x2="12" y2="19"/>
        <line x1="5" y1="12" x2="19" y2="12"/>
    </svg>
);

export default PlusIcon;
