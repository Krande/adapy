// OptionsComponent.tsx
import React, { useEffect, useState } from 'react';
import { Rnd } from 'react-rnd';
import { useAnimationStore } from "../state/animationStore";
import { useOptionsStore } from "../state/optionsStore";
import { useColorStore } from "../state/colorLegendStore";
import { connect_to_jupyter } from "../utils/jupyter_connection";

function OptionsComponent() {
    const { showPerf, setShowPerf } = useOptionsStore();
    const { showLegend, setShowLegend } = useColorStore();

    const [size, setSize] = useState({ width: 300, height: 400 });
    const [position, setPosition] = useState({ x: 0, y: 0 });
    const [isPositionCalculated, setIsPositionCalculated] = useState(false);

    // Set the component to be centered on mount and set the visibility flag
    useEffect(() => {
        const centerX = (window.innerWidth - size.width) / 2;
        const centerY = (window.innerHeight - size.height) / 2;
        setPosition({ x: centerX, y: centerY });

        // Once position is calculated, set the component to be visible
        setIsPositionCalculated(true);
    }, [size]);

    // Don't render the component until the position is calculated
    if (!isPositionCalculated) {
        return null; // Render nothing while waiting for the position to be calculated
    }

    return (
        <Rnd
            size={size}
            position={position}
            onDragStop={(e, d) => setPosition({ x: d.x, y: d.y })}
            onResize={(e, direction, ref, delta, position) => {
                setSize({
                    width: ref.offsetWidth,
                    height: ref.offsetHeight,
                });
                setPosition(position);
            }}
            minWidth={200}
            minHeight={300}
            bounds="parent"
        >
            <div className="flex flex-col space-y-4 p-2 bg-gray-800 rounded shadow-lg h-full">
                <button
                    className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 rounded"}
                    onClick={() => console.log(useAnimationStore.getState())}
                >
                    Print State
                </button>
                <button
                    className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 rounded"}
                    onClick={() => setShowPerf(!showPerf)}
                >
                    Show stats
                </button>
                <button
                    className={"bg-blue-700 hover:bg-blue-700/50 text-white font-bold py-2 rounded"}
                    onClick={() => setShowLegend(!showLegend)}
                >
                    Show ColorLegend
                </button>
                <button
                    className={"bg-blue-700 hidden hover:bg-blue-700/50 text-white font-bold py-2 rounded"}
                    onClick={connect_to_jupyter}
                >
                    Jupyter Test
                </button>
            </div>
        </Rnd>
    );
};

export default OptionsComponent;
