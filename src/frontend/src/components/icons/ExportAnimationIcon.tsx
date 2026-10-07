import React from "react";

// Film frame with a downward arrow — "save the animation". Same 24px / 15-unit
// viewBox as the other icons in this folder so it lines up in the transport row.
const ExportAnimationIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg
        width="24px"
        height="24px"
        viewBox="0 0 15 15"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        {...props}
    >
        <path
            d="M1.5 2.5H13.5V8.5M1.5 2.5V10.5H6.5M4 2.5V10.5M11 2.5V6.5M10.5 9V14M10.5 14L8.5 12M10.5 14L12.5 12"
            stroke="#ffffff"
            strokeLinecap="round"
            strokeLinejoin="round"
        />
    </svg>
);

export default ExportAnimationIcon;
