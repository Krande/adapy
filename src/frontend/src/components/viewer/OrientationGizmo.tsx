import {GizmoHelper, GizmoViewport} from '@react-three/drei';
import React from "react";

const OrientationGizmo = () => {
    return (
        <GizmoHelper
            alignment='bottom-right' // Aligns the gizmo to the bottom-right
        >
            <GizmoViewport axisColors={['red', 'green', 'blue']} labelColor="black"/>
        </GizmoHelper>
    );
};

export default OrientationGizmo;
