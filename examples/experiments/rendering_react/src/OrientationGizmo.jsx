import React from 'react';
import { GizmoHelper, GizmoViewport } from '@react-three/drei';

const OrientationGizmo = () => {
    return (
        <GizmoHelper
            alignment='bottom-right' // Aligns the gizmo to the bottom-right
            margin={[80, 80]} // Adjust margin as needed
        >
            <GizmoViewport axisColors={['red', 'green', 'blue']} labelColor="black" />
        </GizmoHelper>
    );
};

export default OrientationGizmo;
