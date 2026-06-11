import React from 'react';

// Slashed-eye companion to ViewIcon — "hidden" state for the loaded-
// models visibility toggle. Same outline so the two read as one
// control flipping state.
const ViewOffIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="12px" height="12px" viewBox="0 0 24 24" fill="none"
         xmlns="http://www.w3.org/2000/svg" {...props}>
        <path
            d="M12.0012 5C7.52354 5 3.73326 7.94288 2.45898 12C3.73324 16.0571 7.52354 19 12.0012 19C16.4788 19 20.2691 16.0571 21.5434 12C20.2691 7.94291 16.4788 5 12.0012 5Z"
            stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        <line x1="4" y1="20" x2="20" y2="4"
              stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
    </svg>
);

export default ViewOffIcon;
