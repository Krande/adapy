import React from "react";
import {type SpaceMouseButtonAction, useSpaceMouseStore} from "@/state/spaceMouseStore";
import {isWebHidSupported, requestSpaceMouse} from "@/utils/spacemouse/spaceMouseDevice";

// The 3D mouse (SpaceMouse) section of Options: the on/off switch, the connect
// button, and the tuning that only matters once it is on. Off by default, and
// off means the browser is never asked for the device.

// In the store's axis order. Roll is left out: the view stays level, so roll
// moves nothing and inverting it would be a control that does nothing.
const AXIS_LABELS = ["Pan X", "Pan Y", "Zoom", "Tilt", "Spin"];

const BUTTON_ACTIONS: {value: SpaceMouseButtonAction; label: string}[] = [
    {value: "fitAll", label: "Fit all (Shift+A)"},
    {value: "fitSelection", label: "Fit selection (Shift+F)"},
    {value: "none", label: "Nothing"},
];

const STATUS_TEXT: Record<string, string> = {
    off: "Off",
    unsupported: "Not supported in this browser",
    disconnected: "No device connected",
    connecting: "Connecting…",
    connected: "Connected",
    error: "Could not connect",
};

const SpaceMouseOptions: React.FC = () => {
    const s = useSpaceMouseStore();
    const supported = isWebHidSupported();

    return (
        <div className="space-y-2">
            <label
                className="flex items-start space-x-2"
                title={supported ? undefined : "Needs WebHID: use Chrome, Edge or another Chromium-based browser"}
            >
                <input
                    type="checkbox"
                    className="mt-1"
                    checked={s.enabled && supported}
                    disabled={!supported}
                    onChange={() => s.setEnabled(!s.enabled)}
                />
                <span className="leading-tight">
                    Enable 3D mouse (SpaceMouse)
                    <span className="block text-xs text-gray-400">
                        {supported
                            ? "Navigate with a 3Dconnexion 6-DoF device through WebHID; no driver needed."
                            : "Needs WebHID, which this browser does not have: use Chrome, Edge or another Chromium-based browser."}
                    </span>
                </span>
            </label>

            {s.enabled && supported && (
                <div className="space-y-2 pl-6">
                    <div className="text-xs">
                        <span className="text-gray-400">Status: </span>
                        <span data-testid="spacemouse-status">
                            {STATUS_TEXT[s.connection] ?? s.connection}
                            {s.deviceName && s.connection !== "disconnected" ? ` — ${s.deviceName}` : ""}
                        </span>
                        {s.error && <span className="block text-red-300">{s.error}</span>}
                    </div>
                    {s.connection !== "connected" && (
                        <button
                            type="button"
                            className="bg-blue-700 hover:bg-blue-600 text-white text-xs font-semibold py-1 px-2 rounded-sm"
                            onClick={() => void requestSpaceMouse()}
                        >
                            Connect SpaceMouse…
                        </button>
                    )}

                    <label className="flex items-center gap-2 text-xs">
                        <span className="w-20">Sensitivity</span>
                        <input
                            type="range"
                            min={0.1}
                            max={3}
                            step={0.05}
                            value={s.sensitivity}
                            onChange={(e) => s.setSensitivity(parseFloat(e.target.value))}
                            className="flex-1 no-drag"
                        />
                        <span className="w-8 text-right font-mono">{s.sensitivity.toFixed(2)}</span>
                    </label>
                    <label className="flex items-center gap-2 text-xs">
                        <span className="w-20">Dead zone</span>
                        <input
                            type="range"
                            min={0}
                            max={0.4}
                            step={0.01}
                            value={s.deadZone}
                            onChange={(e) => s.setDeadZone(parseFloat(e.target.value))}
                            className="flex-1 no-drag"
                        />
                        <span className="w-8 text-right font-mono">{Math.round(s.deadZone * 100)}%</span>
                    </label>

                    <div className="text-xs">
                        <div className="text-gray-400 mb-1">Invert</div>
                        <div className="grid grid-cols-3 gap-x-2 gap-y-1">
                            {AXIS_LABELS.map((label, i) => (
                                <label key={label} className="flex items-center gap-1">
                                    <input
                                        type="checkbox"
                                        checked={!!s.invert[i]}
                                        onChange={() => s.setInvert(i, !s.invert[i])}
                                    />
                                    <span>{label}</span>
                                </label>
                            ))}
                        </div>
                    </div>

                    <div className="text-xs space-y-1">
                        <label className="flex items-center gap-2">
                            <input type="checkbox" checked={s.dominantAxis} onChange={() => s.setDominantAxis(!s.dominantAxis)}/>
                            <span>Dominant axis only</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input type="checkbox" checked={s.lockRotation} onChange={() => s.setLockRotation(!s.lockRotation)}/>
                            <span>Lock rotation (pan and zoom only)</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input type="checkbox" checked={s.lockPan} onChange={() => s.setLockPan(!s.lockPan)}/>
                            <span>Lock pan and zoom (rotate only)</span>
                        </label>
                    </div>

                    {([
                        ["Button 1", s.button1, s.setButton1],
                        ["Button 2", s.button2, s.setButton2],
                    ] as const).map(([label, value, set]) => (
                        <label key={label} className="flex items-center gap-2 text-xs">
                            <span className="w-20">{label}</span>
                            <select
                                value={value}
                                onChange={(e) => set(e.target.value as SpaceMouseButtonAction)}
                                className="bg-gray-700 text-white text-xs rounded-sm px-2 py-1 flex-1"
                            >
                                {BUTTON_ACTIONS.map((a) => (
                                    <option key={a.value} value={a.value}>{a.label}</option>
                                ))}
                            </select>
                        </label>
                    ))}
                </div>
            )}
        </div>
    );
};

export default SpaceMouseOptions;
