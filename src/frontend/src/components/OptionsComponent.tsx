import React, {useCallback, useEffect, useState} from "react";
import {Rnd} from "react-rnd";
import {runtime} from "@/runtime/config";
import {useOptionsStore} from "@/state/optionsStore";
import ActionButtons from "./options/ActionButtons";
import PointSizeOptions from "./options/PointSizeOptions";
import DisplayOptions from "./options/DisplayOptions";
import ExperimentalOptions from "./options/ExperimentalOptions";
import ShortcutsModal from "./options/ShortcutsModal";

const MOBILE_QUERY = "(max-width: 767px)";

// Layout shell. Each section under options/ owns its state and effects.
function OptionsComponent() {
    const [size] = useState({width: 300, height: 460});
    const [, setPosition] = useState({x: 0, y: 0});
    const [isMobile, setIsMobile] = useState(
        () => typeof window !== "undefined" && window.matchMedia(MOBILE_QUERY).matches
    );
    const setIsOptionsVisible = useOptionsStore((s) => s.setIsOptionsVisible);

    const unique_version_id = runtime.uniqueVersionId();

    const clampPosition = useCallback((pos: {x: number; y: number}) => {
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
        window.addEventListener("resize", centerWindow);
        return () => window.removeEventListener("resize", centerWindow);
    }, [centerWindow]);

    useEffect(() => {
        const mq = window.matchMedia(MOBILE_QUERY);
        const onChange = (e: MediaQueryListEvent) => setIsMobile(e.matches);
        mq.addEventListener("change", onChange);
        return () => mq.removeEventListener("change", onChange);
    }, []);

    const sections = (
        <>
            <ActionButtons/>
            <hr className="border-gray-600"/>
            <PointSizeOptions/>
            <hr className="border-gray-600"/>
            <DisplayOptions/>
            <hr className="border-gray-600"/>
            <ExperimentalOptions/>
            <hr className="border-gray-600"/>
            <ShortcutsModal/>
        </>
    );

    if (isMobile) {
        // Drawer pinned to the left edge, full visible-viewport height.
        // No floating window on phones — there isn't enough room for the
        // panel's contents to be reachable, and a centred float clips
        // off-screen.
        return (
            <div className="fixed inset-y-0 left-0 z-40 w-[85vw] max-w-sm bg-gray-800 text-white text-sm shadow-xl flex flex-col">
                <div className="flex items-center justify-between p-3 border-b border-gray-700">
                    <span className="font-bold text-base">Options</span>
                    <button
                        type="button"
                        onClick={() => setIsOptionsVisible(false)}
                        className="text-gray-300 hover:text-white text-xl leading-none px-2"
                        aria-label="Close options"
                        title="Close"
                    >
                        ×
                    </button>
                </div>
                <div className="flex-1 overflow-y-auto p-4 flex flex-col space-y-4">
                    <div className="text-xs text-gray-400">Version: {unique_version_id}</div>
                    {sections}
                </div>
            </div>
        );
    }

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
            enableResizing={{right: true, bottomRight: true}}
            dragHandleClassName="options-drag-handle"
            cancel="input, button, select, textarea, .no-drag"
            onDragStop={(_e, d) => setPosition({x: d.x, y: d.y})}
            onResizeStop={(_e, _direction, _ref, _delta, position) => setPosition(position)}
        >
            <div
                className="flex flex-col space-y-4 p-4 bg-gray-800 rounded shadow-lg h-full text-white text-sm"
                style={{
                    height: "auto",
                    maxHeight: "640px",
                    overflowY: "auto",
                    width: "100%",
                    boxSizing: "border-box",
                }}
            >
                <div className="options-drag-handle font-bold text-base cursor-move select-none">
                    Options Panel
                </div>
                <div className="text-xs text-gray-400">Version: {unique_version_id}</div>

                {sections}
            </div>
        </Rnd>
    );
}

export default OptionsComponent;
