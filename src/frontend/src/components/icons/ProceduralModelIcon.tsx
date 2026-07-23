import React from 'react';

// Small lattice-of-cubes glyph marking procedural-model rows in the storage
// browser — visually distinct from file/folder icons at a glance.
const ProceduralModelIcon = (props: React.SVGProps<SVGSVGElement>) => (
    <svg width="14px" height="14px" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" {...props}>
        <rect x="1.5" y="1.5" width="5.5" height="5.5" stroke="#a78bfa" strokeWidth="1.3" fill="none"/>
        <rect x="9" y="1.5" width="5.5" height="5.5" stroke="#a78bfa" strokeWidth="1.3" fill="none"/>
        <rect x="1.5" y="9" width="5.5" height="5.5" stroke="#a78bfa" strokeWidth="1.3" fill="none"/>
        <rect x="9" y="9" width="5.5" height="5.5" stroke="#a78bfa" strokeWidth="1.3" fill="#a78bfa" fillOpacity="0.45"/>
    </svg>
);

export default ProceduralModelIcon;
