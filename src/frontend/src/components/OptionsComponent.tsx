import React, { useEffect, useState, useCallback } from 'react';
import { Rnd } from 'react-rnd';
import { useAnimationStore } from "../state/animationStore";
import { useOptionsStore } from "../state/optionsStore";
import { useColorStore } from "../state/colorLegendStore";
import { takeScreenshot } from "../utils/takeScreenshot";
import { loadRobot } from "../utils/robots";
import {useModelStore} from "../state/modelStore";

function OptionsComponent() {
    const {
        showPerf,
        setShowPerf,
        showEdges,
        setShowEdges,
        lockTranslation,
        setLockTranslation,
        setEnableWebsocket,
        enableWebsocket,
    } = useOptionsStore();
    const { showLegend, setShowLegend } = useColorStore();
    const { zIsUp, setZIsUp } = useModelStore();

    const [size, setSize] = useState({ width: 300, height: 460 });
    const [position, setPosition] = useState({ x: 0, y: 0 });
    const [isModalOpen, setIsModalOpen] = useState(false);

    const unique_version_id = (window as any).UNIQUE_VERSION_ID || 0;

    const clampPosition = useCallback((pos: { x: number; y: number }) => {
        const clampedX = Math.min(Math.max(0, pos.x), window.innerWidth - size.width);
        const clampedY = Math.min(Math.max(0, pos.y), window.innerHeight - size.height);
        return { x: clampedX, y: clampedY };
    }, [size]);

    const centerWindow = useCallback(() => {
        const centerX = (window.innerWidth - size.width) / 2;
        const centerY = (window.innerHeight - size.height) / 2;
        setPosition(clampPosition({ x: centerX, y: centerY }));
    }, [size, clampPosition]);

    useEffect(() => {
        centerWindow();
        window.addEventListener('resize', centerWindow);
        return () => {
            window.removeEventListener('resize', centerWindow);
        };
    }, [centerWindow]);

    return (
        <Rnd
            size={size}
            position={position}
            onDragStop={(e, d) => setPosition(clampPosition({ x: d.x, y: d.y }))}
            onResize={(e, direction, ref, delta, pos) => {
                setSize({ width: ref.offsetWidth, height: ref.offsetHeight });
                setPosition(clampPosition(pos));
            }}
            minWidth={250}
            minHeight={300}
            bounds="window"
        >
            <div className="flex flex-col space-y-4 p-4 bg-gray-800 rounded shadow-lg h-full text-white text-sm">
                <div className="font-bold text-base">Options Panel</div>
                <div className="text-xs text-gray-400">Version: {unique_version_id}</div>

                <div className="space-y-2">
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded w-full"
                        onClick={() => console.log(useAnimationStore.getState())}
                    >
                        Print State
                    </button>
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded w-full"
                        onClick={loadRobot}
                    >
                        Load URDF Model
                    </button>
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded w-full"
                        onClick={async () => {
                            try {
                                await takeScreenshot();
                            } catch (error) {
                                console.error("Error taking screenshot:", error);
                            }
                        }}
                    >
                        Take Screenshot
                    </button>
                </div>

                <hr className="border-gray-600" />

                <div className="space-y-2">
                    <label className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            checked={showPerf}
                            onChange={() => setShowPerf(!showPerf)}
                        />
                        <span>Show Stats (Perf)</span>
                    </label>
                    <label className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            checked={showLegend}
                            onChange={() => setShowLegend(!showLegend)}
                        />
                        <span>Show Color Legend</span>
                    </label>
                    <label className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            checked={showEdges}
                            onChange={() => setShowEdges(!showEdges)}
                        />
                        <span>Geometry Edges</span>
                    </label>
                    <label className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            checked={lockTranslation}
                            onChange={() => setLockTranslation(!lockTranslation)}
                        />
                        <span>Lock Translation</span>
                    </label>
                    <label className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            checked={enableWebsocket}
                            onChange={() => setEnableWebsocket(!enableWebsocket)}
                        />
                        <span>Enable Websocket</span>
                    </label>
                    <label className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            checked={zIsUp}
                            onChange={() => setZIsUp(!zIsUp)}
                        />
                        <span>Z is UP</span>
                    </label>
                </div>

                <hr className="border-gray-600" />

                <div>
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded w-full"
                        onClick={() => setIsModalOpen(!isModalOpen)}
                    >
                        Shortcut Keys
                    </button>
                    {isModalOpen && (
                        <div className="mt-2 bg-gray-700 p-2 rounded text-xs space-y-1">
                            <p><kbd>Shift + H</kbd>: Hide</p>
                            <p><kbd>Shift + U</kbd>: Unhide All</p>
                            <p><kbd>Shift + F</kbd>: Center on Selection</p>
                            <p><kbd>Shift + A</kbd>: Zoom to All</p>
                        </div>
                    )}
                </div>
            </div>
        </Rnd>
    );
}

export default OptionsComponent;
