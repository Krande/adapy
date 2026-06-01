import React from "react";
import * as THREE from "three";

import {useSectionStore, type SectionPlane} from "@/state/sectionStore";
import {useModelState} from "@/state/modelState";

// Signed position of the plane along its normal (origin -> plane), the natural
// slider value. constant = -(normal·p), so position = -constant for a unit normal.
function planePosition(p: SectionPlane): number {
    return -p.constant;
}

// Slider range = the model bbox projected onto the plane normal.
function sliderRange(p: SectionPlane): [number, number] {
    const bb = useModelState.getState().boundingBox;
    if (!bb) return [-100, 100];
    const n = new THREE.Vector3(...p.normal);
    const corners = [
        [bb.min.x, bb.min.y, bb.min.z], [bb.max.x, bb.min.y, bb.min.z],
        [bb.min.x, bb.max.y, bb.min.z], [bb.max.x, bb.max.y, bb.min.z],
        [bb.min.x, bb.min.y, bb.max.z], [bb.max.x, bb.min.y, bb.max.z],
        [bb.min.x, bb.max.y, bb.max.z], [bb.max.x, bb.max.y, bb.max.z],
    ].map(([x, y, z]) => n.dot(new THREE.Vector3(x, y, z)));
    const lo = Math.min(...corners);
    const hi = Math.max(...corners);
    const pad = (hi - lo) * 0.02 || 1;
    return [lo - pad, hi + pad];
}

const SectionPlanesPanel = () => {
    const {planes, activeId, capColor, addPlane, removePlane, toggle, setConstant, flip, setActive, setCapColor, clearAll} =
        useSectionStore();
    const [menuId, setMenuId] = React.useState<string | null>(null);
    const hasModel = !!useModelState((s) => s.boundingBox);

    return (
        <div className="p-1 text-sm">
            {!hasModel && <p className="text-xs italic mb-2">Load a model to add section planes.</p>}
            <div className="flex gap-1 mb-2">
                {(["x", "y", "z"] as const).map((ax) => (
                    <button
                        key={ax}
                        className="px-2 py-1 rounded-sm bg-blue-600 text-white disabled:opacity-50"
                        disabled={!hasModel}
                        onClick={() => addPlane(ax)}
                    >
                        + {ax.toUpperCase()}
                    </button>
                ))}
                {planes.length > 0 && (
                    <button className="ml-auto px-2 py-1 rounded-sm bg-gray-600 text-white" onClick={clearAll}>
                        Clear all
                    </button>
                )}
            </div>

            {planes.length === 0 && hasModel && (
                <p className="text-xs italic">No section planes. Add one above.</p>
            )}

            {planes.map((p) => {
                const [lo, hi] = sliderRange(p);
                const pos = planePosition(p);
                return (
                    <div key={p.id} className="mb-2 border-b border-gray-500/40 pb-1">
                        <div className="flex items-center gap-1">
                            <input type="checkbox" checked={p.enabled} onChange={() => toggle(p.id)} title="Enable / disable"/>
                            <label className="flex items-center gap-1 cursor-pointer" title="Attach drag gizmo">
                                <input
                                    type="radio"
                                    name="section-gizmo"
                                    checked={activeId === p.id}
                                    onChange={() => setActive(p.id)}
                                />
                                <span className={p.enabled ? "" : "opacity-50"}>{p.label}</span>
                            </label>
                            <div className="relative ml-auto">
                                <button
                                    className="px-1 rounded-sm hover:bg-gray-500/40"
                                    onClick={() => setMenuId(menuId === p.id ? null : p.id)}
                                    aria-label="Plane menu"
                                >
                                    ⋯
                                </button>
                                {menuId === p.id && (
                                    <div className="absolute right-0 z-10 mt-1 bg-gray-800 text-white text-xs rounded-sm shadow border border-gray-600 min-w-28">
                                        <button className="block w-full text-left px-2 py-1 hover:bg-gray-700" onClick={() => {toggle(p.id); setMenuId(null);}}>
                                            {p.enabled ? "Disable" : "Enable"}
                                        </button>
                                        <button className="block w-full text-left px-2 py-1 hover:bg-gray-700" onClick={() => {flip(p.id); setMenuId(null);}}>
                                            Flip direction
                                        </button>
                                        <button className="block w-full text-left px-2 py-1 hover:bg-gray-700" onClick={() => {setActive(p.id); setMenuId(null);}}>
                                            Attach gizmo
                                        </button>
                                        <button className="block w-full text-left px-2 py-1 hover:bg-gray-700 text-red-300" onClick={() => {removePlane(p.id); setMenuId(null);}}>
                                            Delete
                                        </button>
                                    </div>
                                )}
                            </div>
                        </div>
                        <input
                            type="range"
                            className="w-full"
                            min={lo}
                            max={hi}
                            step={(hi - lo) / 500 || 0.01}
                            value={pos}
                            disabled={!p.enabled}
                            onChange={(e) => {
                                const n = new THREE.Vector3(...p.normal);
                                // position -> constant: constant = -(normal·(position·normal)) = -position for unit normal
                                setConstant(p.id, -Number(e.target.value));
                                void n;
                            }}
                        />
                    </div>
                );
            })}

            {planes.length > 0 && (
                <label className="flex items-center gap-2 mt-2 text-xs">
                    <span>Cap colour</span>
                    <input type="color" value={capColor} onChange={(e) => setCapColor(e.target.value)}/>
                </label>
            )}
        </div>
    );
};

export default SectionPlanesPanel;
