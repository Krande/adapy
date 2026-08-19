import React, {useEffect} from "react";
import {Input, Slider, Switch} from "@/components/ui";
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
        <div className="flex flex-col gap-2">
            <label className="flex flex-col gap-1">
                <span className="text-xs text-content-muted">Point size</span>
                <div className="flex items-center gap-2 min-w-0">
                    <Slider
                        min={min}
                        max={max}
                        step={step}
                        value={pointSize}
                        onValueChange={(n) => setPointSize(n)}
                    />
                    {/* Width goes on a wrapper, not on the Input: Input sets w-full itself,
                        and which of w-full / w-20 wins is decided by stylesheet order, not by
                        prop order. The wrapper makes it deterministic. */}
                    <div className="w-20 shrink-0">
                        <Input
                            fieldSize="sm"
                            mono
                            type="number"
                            min={min}
                            max={max}
                            step={step}
                            value={pointSize}
                            onChange={(e) => setPointSize(parseFloat(e.target.value) || 0)}
                            // `no-drag` keeps react-rnd from dragging the whole panel when
                            // the classic UI floats this in a window.
                            className="no-drag"
                        />
                    </div>
                </div>
            </label>
            <Switch
                label="Absolute point size"
                hint="Sized in world units rather than screen pixels."
                checked={pointSizeAbsolute}
                onChange={() => setPointSizeAbsolute(!pointSizeAbsolute)}
                className="no-drag"
            />
        </div>
    );
};

export default PointSizeOptions;
