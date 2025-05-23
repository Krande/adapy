import React from 'react';

const ToggleControlsIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24px" height="24px" viewBox="0 0 24 24" strokeWidth="1.5" fill="none"
         xmlns="http://www.w3.org/2000/svg" color="#ffffff" {...props}>
        <path
            d="M6 3V21"
            stroke="#ffffff"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
        />
        <circle
            cx="6"
            cy="9"
            r="2"
            stroke="#ffffff"
            strokeWidth="1.5"
        />
        <path
            d="M18 3V21"
            stroke="#ffffff"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
        />
        <circle
            cx="18"
            cy="15"
            r="2"
            stroke="#ffffff"
            strokeWidth="1.5"
        />
    </svg>
);

export default ToggleControlsIcon;
