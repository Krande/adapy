import React from 'react';

// A magnifier over a list: search by attributes, across collections.
const AttributeSearchIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5}
         stroke="currentColor" {...props} className={props.className ?? "w-6 h-6"}>
        <path strokeLinecap="round" strokeLinejoin="round"
              d="M3.75 6h9M3.75 10.5h5.25M3.75 15h3.75M15.75 18.75a3.75 3.75 0 1 0 0-7.5 3.75 3.75 0 0 0 0 7.5Zm2.65-1.1 2.85 2.85"/>
    </svg>
);

export default AttributeSearchIcon;
