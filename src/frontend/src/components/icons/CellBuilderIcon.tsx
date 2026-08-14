import React from 'react';

// 2x2 grid of cells with one "extruding" — the procedural cellbuilder tool.
const CellBuilderIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24px" height="24px" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" {...props}>
        <rect x="1" y="8" width="6" height="6" stroke="#ffffff" strokeWidth="1.4" fill="none"/>
        <rect x="8.6" y="8" width="6" height="6" stroke="#ffffff" strokeWidth="1.4" fill="none"/>
        <rect x="1" y="1" width="6" height="6" stroke="#ffffff" strokeWidth="1.4" fill="#ffffff" fillOpacity="0.35"/>
        <path d="M11.6 6.5V1M11.6 1L9.4 3.2M11.6 1L13.8 3.2" stroke="#ffffff" strokeWidth="1.4" strokeLinecap="round"/>
    </svg>
);

export default CellBuilderIcon;
