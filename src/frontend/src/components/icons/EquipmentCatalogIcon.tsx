import React from 'react';

// A boxed unit with nozzle stubs — the equipment-type catalog.
const EquipmentCatalogIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24" height="24" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" {...props}>
        <rect x="4" y="5" width="8" height="7" stroke="currentColor" strokeWidth="1.4" fill="currentColor" fillOpacity="0.25"/>
        <path d="M8 5V2M2 8.5H4M12 8.5H14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/>
        <circle cx="8" cy="2" r="1.1" fill="currentColor"/>
        <circle cx="2" cy="8.5" r="1.1" fill="currentColor"/>
        <circle cx="14" cy="8.5" r="1.1" fill="currentColor"/>
    </svg>
);

export default EquipmentCatalogIcon;
