import React from "react";
import {useCameraControlsStore} from "@/state/cameraControlsStore";
import {
    CAMERA_ACTIONS, CAMERA_PRESETS, CAMERA_PRESET_IDS, DRAG_MODIFIERS, MOUSE_BUTTONS, ONE_FINGER_ACTIONS, SPEED_MAX, SPEED_MIN,
    TWO_FINGER_ACTIONS, presetOf, type CameraAction, type DragModifier, type OneFingerAction, type TwoFingerAction,
} from "@/utils/camera/cameraMapping";

const ACTION_LABEL: Record<CameraAction, string> = {orbit: "Orbit", pan: "Pan", zoom: "Zoom", none: "Nothing"};
const MODIFIER_LABEL: Record<DragModifier, string> = {none: "Drag", shift: "Shift +", ctrl: "Ctrl +", alt: "Alt +"};
const ONE_FINGER_LABEL: Record<OneFingerAction, string> = {orbit: "Orbit", pan: "Pan", none: "Nothing"};
const TWO_FINGER_LABEL: Record<TwoFingerAction, string> = {"zoom-pan": "Pinch zooms, move pans", "zoom-orbit": "Pinch zooms, move orbits", none: "Nothing"};
const SELECT = "bg-gray-700 text-white text-xs rounded-sm px-1 py-0.5";

const isIn = <T extends string>(list: readonly T[], v: string): v is T => list.some((x) => x === v);

/** Which mouse button, modifier, wheel and touch gesture does what to the camera. Applies at once, on
 *  whichever viewport is on screen. Camera KEYS (named views, fit, orbit and pan by key) are in Shortcut Keys. */
const CameraControlsOptions: React.FC = () => {
    const mapping = useCameraControlsStore((s) => s.mapping);
    const store = useCameraControlsStore.getState();
    const preset = presetOf(mapping);
    const check = (checked: boolean, onChange: (v: boolean) => void, label: string, option: string) => (
        <label className="flex items-center space-x-2">
            <input type="checkbox" data-option={option} checked={checked} onChange={(e) => onChange(e.target.checked)}/>
            <span>{label}</span>
        </label>
    );
    return (
        <div className="space-y-2 text-xs">
            <label className="block space-y-1">
                <span className="text-gray-300">Preset</span>
                <select className={`${SELECT} w-full`} data-option="camera-preset" value={preset}
                    onChange={(e) => { if (isIn(CAMERA_PRESET_IDS, e.target.value)) store.setCameraPreset(e.target.value); }}>
                    {CAMERA_PRESET_IDS.map((id) => <option key={id} value={id}>{CAMERA_PRESETS[id].label}</option>)}
                    {preset === "custom" && <option value="custom">Custom</option>}
                </select>
            </label>
            <table className="w-full" data-option="camera-drag">
                <thead>
                    <tr className="text-gray-400 text-left"><th className="font-normal">Mouse</th>{MOUSE_BUTTONS.map((b) => <th key={b} className="font-normal capitalize">{b}</th>)}</tr>
                </thead>
                <tbody>
                    {DRAG_MODIFIERS.map((modifier) => (
                        <tr key={modifier}>
                            <td className="text-gray-300 pr-1 whitespace-nowrap">{MODIFIER_LABEL[modifier]}</td>
                            {MOUSE_BUTTONS.map((button) => (
                                <td key={button}>
                                    <select className={SELECT} aria-label={`${MODIFIER_LABEL[modifier]} ${button} button`} value={mapping.drag[button][modifier]}
                                        onChange={(e) => { if (isIn(CAMERA_ACTIONS, e.target.value)) store.setDragAction(button, modifier, e.target.value); }}>
                                        {CAMERA_ACTIONS.map((a) => <option key={a} value={a}>{ACTION_LABEL[a]}</option>)}
                                    </select>
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
            {check(mapping.wheel.zoom, store.setWheelZoom, "Wheel zooms", "camera-wheel-zoom")}
            {check(mapping.wheel.inverted, store.setWheelInverted, "Reverse the wheel's direction", "camera-wheel-inverted")}
            {check(mapping.wheel.toCursor, store.setWheelToCursor, "Zoom towards the cursor", "camera-wheel-to-cursor")}
            {check(mapping.damping, store.setCameraDamping, "Glide to a stop", "camera-damping")}
            <label className="flex items-center justify-between space-x-2">
                <span className="text-gray-300">One finger</span>
                <select className={SELECT} data-option="camera-touch-one" value={mapping.touch.one}
                    onChange={(e) => { if (isIn(ONE_FINGER_ACTIONS, e.target.value)) store.setOneFingerAction(e.target.value); }}>
                    {ONE_FINGER_ACTIONS.map((a) => <option key={a} value={a}>{ONE_FINGER_LABEL[a]}</option>)}
                </select>
            </label>
            <label className="flex items-center justify-between space-x-2">
                <span className="text-gray-300">Two fingers</span>
                <select className={SELECT} data-option="camera-touch-two" value={mapping.touch.two}
                    onChange={(e) => { if (isIn(TWO_FINGER_ACTIONS, e.target.value)) store.setTwoFingerAction(e.target.value); }}>
                    {TWO_FINGER_ACTIONS.map((a) => <option key={a} value={a}>{TWO_FINGER_LABEL[a]}</option>)}
                </select>
            </label>
            <label className="flex items-center space-x-2">
                <span className="w-24 text-gray-300">Orbit speed</span>
                <input type="range" data-option="camera-orbit-speed" min={SPEED_MIN} max={SPEED_MAX} step={0.1} value={mapping.orbitSpeed}
                    onChange={(e) => store.setOrbitSpeed(Number(e.target.value))}/>
                <span className="font-mono">{mapping.orbitSpeed.toFixed(1)}</span>
            </label>
            <label className="flex items-center space-x-2">
                <span className="w-24 text-gray-300">Zoom speed</span>
                <input type="range" data-option="camera-zoom-speed" min={SPEED_MIN} max={SPEED_MAX} step={0.1} value={mapping.zoomSpeed}
                    onChange={(e) => store.setZoomSpeed(Number(e.target.value))}/>
                <span className="font-mono">{mapping.zoomSpeed.toFixed(1)}</span>
            </label>
            <div className="text-gray-400">Keys for named views, fit, and orbiting or panning by key are under Shortcut Keys.</div>
        </div>
    );
};

export default CameraControlsOptions;
