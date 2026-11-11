import React from 'react';

/** The Layers panel's glyph: three sheets, one over the other. */
const LayersIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24px" height="24px" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"
         color="currentColor" {...props}>
        <path d="M12 4L3 8.5L12 13L21 8.5L12 4Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
        <path d="M3 12L12 16.5L21 12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
        <path d="M3 15.5L12 20L21 15.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.5"/>
    </svg>
);

export default LayersIcon;
