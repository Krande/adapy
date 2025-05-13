import React from "react";
import {useModelState} from "../../state/modelState";

interface ClickCoordinate {
    x: number;
    y: number;
    z: number;
}

interface CoordinateDisplayProps {
    clickCoordinate: ClickCoordinate | null;
    prec: number;
}

const CoordinateDisplay: React.FC<CoordinateDisplayProps> = ({clickCoordinate, prec}) => {
    const {zIsUp} = useModelState();

    if (!clickCoordinate) {
        return <div className="table-cell w-48">&nbsp;</div>;
    }
    const x = clickCoordinate.x.toFixed(prec);
    const y = clickCoordinate.y.toFixed(prec);
    const z = clickCoordinate.z.toFixed(prec);

    return (
        <div className="table-cell w-48">
            {zIsUp
                ? `(${x}, ${y}, ${z})`
                : `(${x}, ${z}, ${y})`}
        </div>
    );
};

export default CoordinateDisplay;
