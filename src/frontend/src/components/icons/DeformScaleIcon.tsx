import React from "react";

// Two arrows pulling outward from a corner — "scale / exaggerate". Same 15-unit
// viewBox as the other icons in this folder, drawn smaller where it is used inline.
const DeformScaleIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="16px" height="16px" viewBox="0 0 15 15" fill="none" xmlns="http://www.w3.org/2000/svg" {...props}>
        <path
            d="M2.5 12.5L6.5 8.5M2.5 12.5V9M2.5 12.5H6M12.5 2.5L8.5 6.5M12.5 2.5V6M12.5 2.5H9"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
        />
    </svg>
);

export default DeformScaleIcon;
