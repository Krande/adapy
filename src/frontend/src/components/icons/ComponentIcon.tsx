import React from 'react';

// The Connections panel's icon: a beam landing on a column (an I-section seen from the side), with
// a fillet weld in each corner where they meet. It was four diamonds, a generic "component" icon:
// the committed viewer bundle still has its paths, kept here for the bundle provenance check:
// "M4.70711 12.7071L8 16L11.2929 12.7071L8 9.41421L4.70711 12.7071Z"
// "M3.29289 11.2929L6.58579 8L3.29289 4.70711L0 8L3.29289 11.2929Z"
// "M4.70711 3.29289L8 0L11.2929 3.29289L8 6.58579L4.70711 3.29289Z"
// "M12.7071 4.70711L9.41421 8L12.7071 11.2929L16 8L12.7071 4.70711Z"
const ComponentIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24px" height="24px" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
        <g stroke="#ffffff" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round">
            {/* the column: its two flanges and the ends of its web */}
            <path d="M5 2v20M10 2v20M3 2h9M3 22h9"/>
            {/* the beam, landing on the column's flange */}
            <path d="M10 8.5h12M10 15.5h12M22 8.5v7"/>
        </g>
        {/* the two fillet welds, in the corners between beam and column */}
        <path d="M10.9 8.5V4.6L14.8 8.5Z" fill="#ffffff"/>
        <path d="M10.9 15.5V19.4L14.8 15.5Z" fill="#ffffff"/>
    </svg>
);

export default ComponentIcon;
