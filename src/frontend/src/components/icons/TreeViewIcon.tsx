import React from 'react';

const TreeViewIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg fill="currentColor" width="24px" height="24px" viewBox="0 0 36 36" version="1.1"
         preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg"
    >
        <title>tree-view-line</title>
        <path d="M15,32H11a1,1,0,0,1-1-1V27a1,1,0,0,1,1-1h4a1,1,0,0,1,1,1v4A1,1,0,0,1,15,32Zm-3-2h2V28H12Z"
              className="clr-i-outline clr-i-outline-path-1"></path>
        <path
            d="M15,16H11a1,1,0,0,0-1,1v1.2H5.8V12H7a1,1,0,0,0,1-1V7A1,1,0,0,0,7,6H3A1,1,0,0,0,2,7v4a1,1,0,0,0,1,1H4.2V29.8h6.36a.8.8,0,0,0,0-1.6H5.8V19.8H10V21a1,1,0,0,0,1,1h4a1,1,0,0,0,1-1V17A1,1,0,0,0,15,16ZM4,8H6v2H4ZM14,20H12V18h2Z"
            className="clr-i-outline clr-i-outline-path-2"></path>
        <path d="M34,9a1,1,0,0,0-1-1H10v2H33A1,1,0,0,0,34,9Z" className="clr-i-outline clr-i-outline-path-3"></path>
        <path d="M33,18H18v2H33a1,1,0,0,0,0-2Z" className="clr-i-outline clr-i-outline-path-4"></path>
        <path d="M33,28H18v2H33a1,1,0,0,0,0-2Z" className="clr-i-outline clr-i-outline-path-5"></path>
        <rect x="0" y="0" width="36" height="36" fillOpacity="0"/>
    </svg>
);

export default TreeViewIcon;