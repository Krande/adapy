import React from 'react';

// Connected nodes with a routed run — the system-template catalog.
const SystemCatalogIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" {...props}>
        <path d="M3 3V8H13V13" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round"/>
        <circle cx="3" cy="3" r="2" stroke="currentColor" strokeWidth="1.4" fill="none"/>
        <circle cx="13" cy="13" r="2" stroke="currentColor" strokeWidth="1.4" fill="none"/>
        <circle cx="8" cy="8" r="1.4" fill="currentColor"/>
    </svg>
);

export default SystemCatalogIcon;
