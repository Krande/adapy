import React from 'react';

// A boxed unit with nozzle stubs — the equipment-type catalog.
const EquipmentCatalogIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24px" height="24px" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" {...props}>
        <rect x="4" y="5" width="8" height="7" stroke="#ffffff" strokeWidth="1.4" fill="#ffffff" fillOpacity="0.25"/>
        <path d="M8 5V2M2 8.5H4M12 8.5H14" stroke="#ffffff" strokeWidth="1.4" strokeLinecap="round"/>
        <circle cx="8" cy="2" r="1.1" fill="#ffffff"/>
        <circle cx="2" cy="8.5" r="1.1" fill="#ffffff"/>
        <circle cx="14" cy="8.5" r="1.1" fill="#ffffff"/>
    </svg>
);

export default EquipmentCatalogIcon;
