import React from "react";
import {OVERRIDE_KEYS, OverrideKey, OverrideTri} from "./conversionOverrides";

// The per-conversion override tri-states, shown under the toolbar when the
// Overrides button is open.
const OverridesPanel: React.FC<{
    overrides: Record<OverrideKey, OverrideTri>;
    onChange: (key: OverrideKey, value: OverrideTri) => void;
}> = ({overrides, onChange}) => (
    <div className="px-3 sm:px-4 py-2 border-b border-gray-700 bg-gray-900/40 text-[11px]">
        <div className="text-gray-400 mb-2">
            Overrides apply to every Convert click on this tab. ``Unset`` →
            worker uses the global setting (or adapy's code default).
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {OVERRIDE_KEYS.map(({key, label}) => (
                <div key={key} className="flex items-center gap-2">
                    <span className="flex-1 truncate" title={key}>{label}</span>
                    <div className="inline-flex rounded-sm overflow-hidden">
                        {(["unset", "on", "off"] as OverrideTri[]).map((v) => (
                            <button
                                key={v}
                                onClick={() => onChange(key, v)}
                                className={
                                    "px-2 py-0.5 border text-[10px] " +
                                    (overrides[key] === v
                                        ? "bg-blue-700 text-white border-blue-500"
                                        : "bg-gray-800 text-gray-200 border-gray-700 hover:bg-gray-700")
                                }
                            >
                                {v === "unset" ? "—" : v === "on" ? "On" : "Off"}
                            </button>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    </div>
);

export default OverridesPanel;
