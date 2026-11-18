import React from "react";

import {useSectionStore, type SectionPlane} from "@/state/sectionStore";
import {useModelState} from "@/state/modelState";
import {useSceneFrameStore, type SceneOffset} from "@/state/sceneFrameStore";
import {SectionPositionInput} from "@/layout/SectionPositionInput";
import {sectionAxis, sectionConstant} from "@/utils/scene/sectionOffset";
import {activeViewport} from "@/viewport/api/viewportApi";
import {useNativeViewport} from "@/viewport/native/nativeModel";

const AXIS_LABELS = ["X", "Y", "Z"] as const;

// The plane's position on its axis and the slider's range, in model coordinates: the same numbers as
// the section strip's, flipped or not (utils/scene/sectionOffset). The range is the box of the model
// on screen, whichever viewport draws it, padded so either end clears the model.
const planeAxis = (p: SectionPlane, offset: SceneOffset | null) => sectionAxis(p, activeViewport()?.modelBounds() ?? null, offset);

const SectionPlanesPanel = () => {
    const {planes, activeId, gizmoVisible, capColor, addPlane, removePlane, toggle, setConstant, flip, setActive, setGizmoVisible, setCapColor, clearAll} =
        useSectionStore();
    const [menuId, setMenuId] = React.useState<string | null>(null);
    // Either viewport's model counts; these two subscriptions are what re-renders the panel when one arrives.
    const threeModel = useModelState((s) => s.boundingBox);
    const nativeModel = useNativeViewport((s) => s.model);
    const hasModel = !!threeModel || !!nativeModel;
    const offset = useSceneFrameStore((s) => s.offset);

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
                const at = planeAxis(p, offset);
                const {lo, hi, position: pos} = at;
                const moveTo = (v: number) => setConstant(p.id, sectionConstant(at, v, offset));
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
                            onChange={(e) => moveTo(Number(e.target.value))}
                        />
                        <SectionPositionInput
                            position={pos}
                            axisLabel={AXIS_LABELS[at.axis]}
                            disabled={!p.enabled}
                            className="w-20 rounded-sm bg-black/20 px-1 text-xs"
                            onCommit={moveTo}
                        />
                    </div>
                );
            })}

            {planes.length > 0 && (
                <div className="mt-2 space-y-1 text-xs">
                    <label className="flex items-center gap-2">
                        <input type="checkbox" checked={gizmoVisible} onChange={(e) => setGizmoVisible(e.target.checked)}/>
                        <span>Show gizmo</span>
                    </label>
                    <label className="flex items-center gap-2">
                        <span>Cap colour</span>
                        <input type="color" value={capColor} onChange={(e) => setCapColor(e.target.value)}/>
                    </label>
                </div>
            )}
        </div>
    );
};

export default SectionPlanesPanel;
