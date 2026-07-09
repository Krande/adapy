import React from "react";

// A subdivided triangle — the Mesh panel inspects triangulated geometry (tessellation quality /
// crows-nest spikes), so a triangle split into inner triangles reads as "mesh / tessellation".
const MeshIcon = () => {
    return (
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M12 3 22 20H2L12 3Z"/>
            <path d="M12 3v17"/>
            <path d="M7 11.5 17 11.5"/>
        </svg>
    );
};

export default MeshIcon;
