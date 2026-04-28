import React from "react";

// Tray with an upward arrow — the "upload" gesture in most modern UI.
// Matches the 24px / 15-unit viewBox the other icons in this folder
// use so they line up flush in toolbars.
const UploadIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg
        width="24px"
        height="24px"
        viewBox="0 0 15 15"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        {...props}
    >
        <path
            d="M7.5 1.5L7.5 9.5M7.5 1.5L4.5 4.5M7.5 1.5L10.5 4.5M2.5 9.5V12.5C2.5 13.0523 2.94772 13.5 3.5 13.5H11.5C12.0523 13.5 12.5 13.0523 12.5 12.5V9.5"
            stroke="#ffffff"
            strokeLinecap="round"
            strokeLinejoin="round"
        />
    </svg>
);

export default UploadIcon;
