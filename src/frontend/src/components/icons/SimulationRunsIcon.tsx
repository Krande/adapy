import React from 'react';

/** The Simulations panel's glyph: a run (a play mark) in a frame with a progress line under it. */
const SimulationRunsIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24px" height="24px" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"
         color="currentColor" {...props}>
        <rect x="3" y="3.5" width="18" height="13" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
        <path d="M10 7.2V12.8L14.8 10L10 7.2Z" fill="currentColor"/>
        <path d="M3 20.5H21" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" opacity="0.4"/>
        <path d="M3 20.5H13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
);

export default SimulationRunsIcon;
