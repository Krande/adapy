// The right-click menu of a selection box: which elements it takes, requesting their geometry from
// the source, how it is drawn, and hiding or deleting it.

import React, {useLayoutEffect, useRef, useState} from "react";
import {createPortal} from "react-dom";

import {useBBoxSelectionStore, type SelectionBox} from "@/state/bboxSelectionStore";
import type {BoxSelectMode} from "@/utils/bboxSelection/boxMath";
import {applyBoxSelection} from "@/utils/bboxSelection/applyBoxSelection";

import {BoxRequestSection} from "./BoxRequestSection";

const MODES: {mode: BoxSelectMode; label: string; title: string}[] = [
    {mode: "partial", label: "Partially within", title: "Select every element the box touches"},
    {mode: "full", label: "Fully within", title: "Select only elements entirely inside the box"},
];

export const BBoxSelectionMenuHost: React.FC = () => {
    const menu = useBBoxSelectionStore((s) => s.menu);
    const box = useBBoxSelectionStore((s) => s.boxes.find((b) => b.id === s.menu?.boxId) ?? null);
    const close = useBBoxSelectionStore((s) => s.closeMenu);
    if (!menu || !box) return null;
    return <BBoxSelectionMenu box={box} x={menu.x} y={menu.y} onClose={close}/>;
};

const itemClass = "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-gray-700";

const BBoxSelectionMenu: React.FC<{box: SelectionBox; x: number; y: number; onClose: () => void}> = ({box, x, y, onClose}) => {
    const ref = useRef<HTMLDivElement>(null);
    const [style, setStyle] = useState<React.CSSProperties>({top: y, left: x, visibility: "hidden"});
    const update = useBBoxSelectionStore((s) => s.update);
    const remove = useBBoxSelectionStore((s) => s.remove);

    useLayoutEffect(() => {
        const el = ref.current;
        const w = el?.offsetWidth ?? 200;
        const h = el?.offsetHeight ?? 200;
        setStyle({top: Math.max(8, Math.min(y, window.innerHeight - h - 8)), left: Math.max(8, Math.min(x, window.innerWidth - w - 8))});
        const outside = (e: Event) => {
            if (!ref.current?.contains(e.target as Node)) onClose();
        };
        const escape = (e: KeyboardEvent) => {
            if (e.key === "Escape") onClose();
        };
        document.addEventListener("mousedown", outside);
        document.addEventListener("touchstart", outside);
        document.addEventListener("keydown", escape);
        return () => {
            document.removeEventListener("mousedown", outside);
            document.removeEventListener("touchstart", outside);
            document.removeEventListener("keydown", escape);
        };
    }, [x, y, onClose]);

    return createPortal(
        <div
            ref={ref}
            role="menu"
            className="fixed z-[70] min-w-[200px] rounded-sm border border-gray-700 bg-gray-800 py-1 text-gray-100 shadow-lg"
            style={style}
            onContextMenu={(e) => e.preventDefault()}
        >
            <div className="border-b border-gray-700 px-3 py-1.5 text-[11px] text-gray-400">{box.label}</div>
            {MODES.map((m) => (
                <button
                    key={m.mode}
                    type="button"
                    role="menuitemradio"
                    aria-checked={box.mode === m.mode}
                    className={itemClass}
                    title={m.title}
                    onClick={() => update(box.id, {mode: m.mode})}
                >
                    <span className="inline-block w-3.5 text-center">{box.mode === m.mode ? "✓" : ""}</span>
                    <span>{m.label}</span>
                </button>
            ))}
            <button type="button" className={itemClass} onClick={() => applyBoxSelection(box.id)} title="Select what the box takes now, e.g. after loading or unhiding geometry">
                <span className="inline-block w-3.5"/>
                <span>Reselect</span>
            </button>
            <div className="my-1 border-t border-gray-700"/>
            <BoxRequestSection box={box}/>
            <div className="my-1 border-t border-gray-700"/>
            <label className="flex items-center gap-2 px-3 py-1.5 text-xs">
                <span className="w-16">Colour</span>
                <input
                    type="color"
                    value={box.color}
                    onChange={(e) => update(box.id, {color: e.target.value})}
                    className="h-5 w-10 cursor-pointer rounded-sm border border-gray-600 bg-transparent"
                />
            </label>
            <label className="flex items-center gap-2 px-3 py-1.5 text-xs">
                <span className="w-16">Opacity</span>
                <input
                    type="range"
                    min={0}
                    max={0.9}
                    step={0.05}
                    value={box.opacity}
                    onChange={(e) => update(box.id, {opacity: Number(e.target.value)})}
                    className="w-24 accent-blue-500"
                />
                <span className="w-8 text-right tabular-nums">{Math.round(box.opacity * 100)}%</span>
            </label>
            <div className="my-1 border-t border-gray-700"/>
            <button type="button" className={itemClass} onClick={() => { update(box.id, {visible: false}); onClose(); }}>
                <span className="inline-block w-3.5"/>
                <span>Hide box</span>
            </button>
            <button
                type="button"
                className={itemClass + " text-red-400 hover:text-red-300"}
                onClick={() => { remove(box.id); onClose(); }}
            >
                <span className="inline-block w-3.5"/>
                <span>Delete box</span>
            </button>
        </div>,
        document.body,
    );
};

export default BBoxSelectionMenuHost;
