// OptionsComponent.tsx
import React, {useEffect, useState} from 'react';
import {Rnd} from 'react-rnd';
import {useAnimationStore} from "../state/animationStore";
import {useOptionsStore} from "../state/optionsStore";
import {useColorStore} from "../state/colorLegendStore";

function OptionsComponent() {
    const {showPerf, setShowPerf, showEdges, setShowEdges} = useOptionsStore();
    const {showLegend, setShowLegend} = useColorStore();

    const [size, setSize] = useState({width: 300, height: 400});
    const [position, setPosition] = useState({x: 0, y: 0});
    const [isPositionCalculated, setIsPositionCalculated] = useState(false);
    const [isModalOpen, setIsModalOpen] = useState(false);

    // Set the component to be centered on mount and set the visibility flag
    useEffect(() => {
        const centerX = (window.innerWidth - size.width) / 2;
        const centerY = (window.innerHeight - size.height) / 2;
        setPosition({x: centerX, y: centerY});

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
            onDragStop={(e, d) => setPosition({x: d.x, y: d.y})}
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
                    className={`bg-blue-700 hover:bg-blue-700/50 ${showEdges ? 'text-white' : 'text-gray-400'} font-bold py-2 rounded`}
                    onClick={() => setShowEdges(!showEdges)}
                >
                    Geometry Edges On/Off
                </button>
                <div className={"flex flex-col w-full"}>
                    <button
                        className={"bg-blue-700 hover:bg-blue-700/50 text-white w-full font-bold py-2 rounded"}
                        onClick={() => setIsModalOpen(!isModalOpen)}
                    >
                        Shortcut Keys
                    </button>
                    {isModalOpen && (
                        <div className="items-center justify-center">
                            <div className="bg-gray-800 text-white p-4 rounded shadow-lg">
                                <p>shift+h: Hide</p>
                                <p>shift+u: Unhide all</p>
                                <p>shift+f: Center view on selection</p>
                                <p>shift+a: Zoom to all</p>
                            </div>
                        </div>
                    )}
                </div>

            </div>
        </Rnd>
    );
};

export default OptionsComponent;
