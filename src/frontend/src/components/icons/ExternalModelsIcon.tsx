import React from 'react';

// A box with an outbound arrow — geometry sourced from somewhere this viewer
// does not manage.
const ExternalModelsIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="24px" height="24px" strokeWidth="1.5" viewBox="0 0 24 24" fill="none"
         xmlns="http://www.w3.org/2000/svg" color="currentColor" {...props}>
        <path d="M12 22L3 17V7L12 2L21 7V11" stroke="currentColor" strokeWidth="1.5"
              strokeLinecap="round" strokeLinejoin="round"></path>
        <path d="M12 22V12M12 12L21 7M12 12L3 7" stroke="currentColor" strokeWidth="1.5"
              strokeLinecap="round" strokeLinejoin="round"></path>
        <path d="M15 19H21M21 19L18.5 16.5M21 19L18.5 21.5" stroke="currentColor" strokeWidth="1.5"
              strokeLinecap="round" strokeLinejoin="round"></path>
    </svg>
);

export default ExternalModelsIcon;
