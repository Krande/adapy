import React from 'react';

const FEMDataPanelIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24px" height="24px" viewBox="0 0 24 24" strokeWidth="1.5" fill="none"
         xmlns="http://www.w3.org/2000/svg" color="#ffffff" {...props}>
        {/* Table outline */}
        <rect x="3" y="4" width="18" height="16" rx="1" stroke="#ffffff" strokeWidth="1.5" />

        {/* Vertical lines */}
        <line x1="9" y1="4" x2="9" y2="20" stroke="#ffffff" strokeWidth="1.5" />
        <line x1="15" y1="4" x2="15" y2="20" stroke="#ffffff" strokeWidth="1.5" />

        {/* Horizontal lines */}
        <line x1="3" y1="10" x2="21" y2="10" stroke="#ffffff" strokeWidth="1.5" />
        <line x1="3" y1="16" x2="21" y2="16" stroke="#ffffff" strokeWidth="1.5" />

        {/* Mini line plot in one cell */}
        <polyline points="10.5,17 11.5,15 12.5,16 13.5,14" stroke="#ffffff" strokeWidth="1.5" fill="none" />
    </svg>
);

export default FEMDataPanelIcon;
