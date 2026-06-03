import React, {useEffect} from "react";
import {useOptionsStore} from "@/state/optionsStore";
import {updateAllPointsSize} from "@/utils/scene/updatePointSizes";

const PointSizeOptions: React.FC = () => {
    const {pointSize, setPointSize, pointSizeAbsolute, setPointSizeAbsolute} = useOptionsStore();

    // Update points in the scene when value/mode changes.
    useEffect(() => {
        updateAllPointsSize(pointSize, pointSizeAbsolute);
    }, [pointSize, pointSizeAbsolute]);

    // Keep absolute sizing correct on viewport resize.
    useEffect(() => {
        const onResize = () => updateAllPointsSize(pointSize, pointSizeAbsolute);
        window.addEventListener("resize", onResize);
        return () => window.removeEventListener("resize", onResize);
    }, [pointSize, pointSizeAbsolute]);

    // Clamp into the active range when the mode flips.
    useEffect(() => {
        if (pointSizeAbsolute) {
            if (pointSize > 0.1 || pointSize < 0.005) setPointSize(0.01);
        } else {
            if (pointSize < 5 || pointSize > 30) setPointSize(10);
        }
    }, [pointSizeAbsolute]);

    const min = pointSizeAbsolute ? 0.005 : 5;
    const max = pointSizeAbsolute ? 0.1 : 30;
    const step = pointSizeAbsolute ? 0.005 : 1;

    return (
        <div className="space-y-2">
            <label className="flex items-center space-x-2">
                <span className="w-32">Point Size</span>
                <input
                    type="range"
                    min={min} max={max} step={step}
                    value={pointSize}
                    onChange={(e) => setPointSize(parseFloat(e.target.value))}
                    className="flex-1 no-drag"
                />
                <input
                    type="number"
                    min={min} max={max} step={step}
                    value={pointSize}
                    onChange={(e) => setPointSize(parseFloat(e.target.value) || 0)}
                    className="w-24 bg-gray-700 text-white p-1 rounded-sm no-drag"
                />
            </label>
            <label className="flex items-center space-x-2">
                <input
                    type="checkbox"
                    className="no-drag"
                    checked={pointSizeAbsolute}
                    onChange={() => setPointSizeAbsolute(!pointSizeAbsolute)}
                />
                <span>Absolute point size (world units)</span>
            </label>
        </div>
    );
};

export default PointSizeOptions;
