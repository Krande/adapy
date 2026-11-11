// The orientation gizmo of the three.js-free viewport: the world's axes as the camera sees them, in the
// corner where the three.js viewport has its own. Clicking an axis end looks at the model from there.
import React from "react";

import type {NativeScene} from "./nativeScene";
import {axisEnds, type AxisEnd} from "./orientationGizmo";

/** As the three.js viewport's gizmo: X red, Y green, Z blue. */
const COLOURS = ["#e5484d", "#7ac231", "#2f8fe8"] as const;
const SIZE = 112;
const REACH = 38;
const DOT = 10;

export const NativeOrientationGizmo: React.FC<{scene: NativeScene}> = ({scene}) => {
    const [ends, setEnds] = React.useState<AxisEnd[]>([]);
    React.useEffect(() => {
        let last = "";
        const read = () => {
            const {right, up, back} = scene.cameraAxes();
            const next = axisEnds(right, up, back);
            // Only a camera that has turned is a new picture; a pan or a zoom is not.
            const key = next.map((e) => `${e.label}:${e.x.toFixed(3)},${e.y.toFixed(3)}`).join(";");
            if (key === last) return;
            last = key;
            setEnds(next);
        };
        read();
        scene.onFrame.add(read);
        return () => void scene.onFrame.delete(read);
    }, [scene]);

    const centre = SIZE / 2;
    return (
        <svg
            width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} role="group" aria-label="View orientation"
            className="absolute right-3 bottom-3 z-10 select-none" data-native-orientation-gizmo="true"
        >
            {ends.filter((e) => e.positive).map((e) => (
                <line key={`l${e.label}`} x1={centre} y1={centre} x2={centre + e.x * REACH} y2={centre + e.y * REACH} stroke={COLOURS[e.axis]} strokeWidth={2}/>
            ))}
            {ends.map((e) => (
                <g
                    key={e.label} role="button" aria-label={`View from ${e.label}`} style={{cursor: "pointer"}}
                    // The viewport's input surface is under the gizmo: the press must not start a camera drag.
                    onPointerDown={(ev) => ev.stopPropagation()}
                    onClick={() => scene.viewFrom(e.direction)}
                >
                    <circle cx={centre + e.x * REACH} cy={centre + e.y * REACH} r={DOT} fill={COLOURS[e.axis]} fillOpacity={e.positive ? 1 : 0.45}/>
                    <text x={centre + e.x * REACH} y={centre + e.y * REACH + 3.5} textAnchor="middle" fontSize={9} fontWeight={700} fill="#111">{e.label}</text>
                </g>
            ))}
        </svg>
    );
};
