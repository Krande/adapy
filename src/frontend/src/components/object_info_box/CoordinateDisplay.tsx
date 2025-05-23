import React from "react";
import { useModelState } from "../../state/modelState";
import CopyIcon from "../icons/copyIcon";

interface ClickCoordinate {
  x: number;
  y: number;
  z: number;
}

interface CoordinateDisplayProps {
  clickCoordinate: ClickCoordinate | null;
  prec: number;
}

const CoordinateDisplay: React.FC<CoordinateDisplayProps> = ({
  clickCoordinate,
  prec,
}) => {
  const { zIsUp } = useModelState();

  if (!clickCoordinate) {
    return <div className="table-cell w-48">&nbsp;</div>;
  }

  const x = clickCoordinate.x.toFixed(prec);
  const y = clickCoordinate.y.toFixed(prec);
  const z = clickCoordinate.z.toFixed(prec);

  // This is the string we'll copy: x,y,z or x,z,y depending on zIsUp
  const copyText = zIsUp ? `${x},${y},${z}` : `${x},${z},${y}`;

  const handleCopy = () => {
    navigator.clipboard
      .writeText(copyText)
      .catch((err) => {
        console.error("Failed to copy coordinates: ", err);
      });
  };

  const displayText = zIsUp
    ? `(${x}, ${y}, ${z})`
    : `(${x}, ${z}, ${y})`;

  return (
    <div className="table-cell w-48 flex items-center pointer-events-auto">
      <span>{displayText}</span>
      {/*<CopyIcon*/}
      {/*  onClick={handleCopy}*/}
      {/*  className="w-5 h-5 ml-2 cursor-pointer text-gray-500 hover:text-gray-700 "*/}
      {/*/>*/}
    </div>
  );
};

export default CoordinateDisplay;
