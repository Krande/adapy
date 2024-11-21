import {GizmoHelper, GizmoViewcube, GizmoViewport} from '@react-three/drei';
import React from "react";

const OrientationGizmo = () => {
    return (
        <GizmoHelper
            alignment='bottom-right' // Aligns the gizmo to the bottom-right
            // Rotate the gizmo so that Z is treated as up
            //matrix={[1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1]}
        >
            {/*<GizmoViewport axisColors={['red', 'blue', 'green']} labelColor="black" labels={["x", "z", "y"]}/>*/}
            <GizmoViewcube color={"blue"} textColor={"white"}/>
        </GizmoHelper>
    );
};

export default OrientationGizmo;
