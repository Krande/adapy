import React, {useCallback, useEffect, useState} from 'react';
import {Rnd} from 'react-rnd';
import {useOptionsStore} from "../state/optionsStore";
import {useColorStore} from "../state/colorLegendStore";
import {takeScreenshot} from "../utils/takeScreenshot";
import {loadRobot} from "../utils/robots";
import {useModelState} from "../state/modelState";
import {debug_print} from "../utils/debug_print";
import {updateAllPointsSize} from "../utils/scene/updatePointSizes";

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
        enableNodeEditor,
        setEnableNodeEditor,
        pointSize,
        setPointSize,
        pointSizeAbsolute,
        setPointSizeAbsolute,
    } = useOptionsStore();
    const {showLegend, setShowLegend} = useColorStore();
    const {zIsUp, setZIsUp, defaultOrbitController, setDefaultOrbitController} = useModelState();

    const [size] = useState({width: 300, height: 460});
    const [position, setPosition] = useState({x: 0, y: 0});
    const [isModalOpen, setIsModalOpen] = useState(false);

    const unique_version_id = (window as any).UNIQUE_VERSION_ID || 0;

    const clampPosition = useCallback((pos: { x: number; y: number }) => {
        const clampedX = Math.min(Math.max(0, pos.x), window.innerWidth - size.width);
        const clampedY = Math.min(Math.max(0, pos.y), window.innerHeight - size.height);
        return {x: clampedX, y: clampedY};
    }, [size]);

    const centerWindow = useCallback(() => {
        const centerX = (window.innerWidth - size.width) / 2;
        const centerY = (window.innerHeight - size.height) / 2;
        setPosition(clampPosition({x: centerX, y: centerY}));
    }, [size, clampPosition]);

    useEffect(() => {
        centerWindow();
        window.addEventListener('resize', centerWindow);
        return () => {
            window.removeEventListener('resize', centerWindow);
        };
    }, [centerWindow]);

    // Update point sizes in the scene whenever the option changes
    useEffect(() => {
        updateAllPointsSize(pointSize, pointSizeAbsolute);
    }, [pointSize, pointSizeAbsolute]);

    // Keep absolute sizing correct on viewport resize
    useEffect(() => {
        const onResize = () => updateAllPointsSize(pointSize, pointSizeAbsolute);
        window.addEventListener('resize', onResize);
        return () => window.removeEventListener('resize', onResize);
    }, [pointSize, pointSizeAbsolute]);

    // Clamp point size into valid UI range upon toggling mode to avoid out-of-range values
    useEffect(() => {
        if (pointSizeAbsolute) {
            if (pointSize > 0.1 || pointSize < 0.005) {
                setPointSize(0.01);
            }
        } else {
            if (pointSize < 5 || pointSize > 30) {
                setPointSize(10);
            }
        }
    }, [pointSizeAbsolute]);

    return (
        <Rnd
            default={{
                width: 300,
                height: 460,
                x: (window.innerWidth - 300) / 2,
                y: (window.innerHeight - 460) / 2,
            }}
            minWidth={250}
            bounds="window"
            enableResizing={{
                right: true,
                bottomRight: true,
            }}
            dragHandleClassName="options-drag-handle"
            cancel="input, button, select, textarea, .no-drag"
            onDragStop={(e, d) => setPosition({x: d.x, y: d.y})}
            onResizeStop={(e, direction, ref, delta, position) => {
                setPosition(position);
            }}
        >
            <div
                className="flex flex-col space-y-4 p-4 bg-gray-800 rounded shadow-lg h-full text-white text-sm"
                style={{
                    height: 'auto',
                    maxHeight: '640px',
                    overflowY: 'auto',
                    width: '100%', // take the width set by Rnd
                    boxSizing: 'border-box',
                }}

            >
                <div className="options-drag-handle font-bold text-base cursor-move select-none">Options Panel</div>
                <div className="text-xs text-gray-400">Version: {unique_version_id}</div>

                <div className="space-y-2">
                    <button
                        className="bg-blue-700 hover:bg-blue-600 text-white font-semibold py-1 px-2 rounded w-full"
                        onClick={() => debug_print()}
                    >
                        Debug print
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

                <hr className="border-gray-600"/>

                <div className="space-y-2">
                    <label className="flex items-center space-x-2">
                        <span className="w-32">Point Size</span>
                        <input
                            type="range"
                            min={pointSizeAbsolute ? 0.005 : 5}
                            max={pointSizeAbsolute ? 0.1 : 30}
                            step={pointSizeAbsolute ? 0.005 : 1}
                            value={pointSize}
                            onChange={(e) => setPointSize(parseFloat(e.target.value))}
                            className="flex-1 no-drag"
                        />
                        <input
                            type="number"
                            min={pointSizeAbsolute ? 0.005 : 5}
                            max={pointSizeAbsolute ? 0.1 : 30}
                            step={pointSizeAbsolute ? 0.005 : 1}
                            value={pointSize}
                            onChange={(e) => setPointSize(parseFloat(e.target.value) || 0)}
                            className="w-24 bg-gray-700 text-white p-1 rounded no-drag"
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

                <hr className="border-gray-600"/>

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
                            checked={enableNodeEditor}
                            onChange={() => setEnableNodeEditor(!enableNodeEditor)}
                        />
                        <span>Enable Node Editor</span>
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
                    <label className="flex items-center space-x-2">
                        <input
                            type="checkbox"
                            checked={defaultOrbitController}
                            onChange={() => setDefaultOrbitController(!defaultOrbitController)}
                        />
                        <span>Use Default Orbitcontroller</span>
                    </label>
                </div>

                <hr className="border-gray-600"/>

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
                            <p><kbd>Shift + Q</kbd>: Toggle Options Menu</p>
                            <p><kbd>Shift + C</kbd>: Copy Selection to Clipboard</p>
                        </div>
                    )}
                </div>
            </div>
        </Rnd>
    );
}

export default OptionsComponent;
