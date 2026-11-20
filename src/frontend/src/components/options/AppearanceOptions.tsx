import React from "react";

import {
    APPEARANCE_DEFAULTS,
    BRIGHTNESS_RANGE,
    EDGE_BRIGHTNESS_RANGE,
    appearanceIsDefault,
    edgeLookIsDefault,
    useAppearanceStore,
} from "@/state/appearanceStore";

// How the model looks, whichever renderer draws it (state/appearanceStore.ts). Every control takes
// effect at once, on the model that is on screen: nothing is loaded again.

const Row: React.FC<{label: string; title?: string; children: React.ReactNode}> = ({label, title, children}) => (
    <label className="flex items-center justify-between gap-2" title={title}>
        <span>{label}</span>
        {children}
    </label>
);

const swatch = "h-6 w-10 cursor-pointer rounded-sm border border-gray-600 bg-transparent no-drag";

const AppearanceOptions: React.FC = () => {
    const s = useAppearanceStore();
    const {setDefaultColor, setBrightness, setAmbientShare, setBackground, setShadeResults, setEdgeColor, setEdgeBrightness, reset, resetEdges} = s;
    const percent = Math.round(s.ambientShare * 100);
    return (
        <div className="space-y-2">
            <Row label="Default colour" title="The colour of geometry that has none of its own">
                <input type="color" data-option="default-colour" value={s.defaultColor} onChange={(e) => setDefaultColor(e.target.value)} className={swatch}/>
            </Row>
            <Row label="Background">
                <input type="color" data-option="background-colour" value={s.background} onChange={(e) => setBackground(e.target.value)} className={swatch}/>
            </Row>
            <Row label={`Brightness ${s.brightness.toFixed(2)}`} title={`Multiplies both lights; ${APPEARANCE_DEFAULTS.brightness.toFixed(2)} is the default`}>
                <input
                    type="range"
                    data-option="brightness"
                    min={BRIGHTNESS_RANGE.min}
                    max={BRIGHTNESS_RANGE.max}
                    step={BRIGHTNESS_RANGE.step}
                    value={s.brightness}
                    onChange={(e) => setBrightness(Number(e.target.value))}
                    className="w-28 cursor-pointer no-drag"
                />
            </Row>
            <Row
                label={`Ambient ${percent}% / headlight ${100 - percent}%`}
                title="How the light is shared between the ambient light (the same on every face) and the headlight (from the camera, which shows the shape)"
            >
                <input
                    type="range"
                    data-option="light-balance"
                    min={0}
                    max={1}
                    step={0.01}
                    value={s.ambientShare}
                    onChange={(e) => setAmbientShare(Number(e.target.value))}
                    className="w-28 cursor-pointer no-drag"
                />
            </Row>
            <label
                className="flex items-center space-x-2"
                title="Off: a result colour is drawn exactly as the legend shows it, whichever way its face is turned. On: the result is lit like the model, which shows its shape and darkens its colours."
            >
                <input type="checkbox" className="no-drag" data-option="shade-results" checked={s.shadeResults} onChange={() => setShadeResults(!s.shadeResults)}/>
                <span>Shade FEA results</span>
            </label>
            <Row label="FEA edge colour" title="The colour of a result's element edges: the shell grid and the beam solids' edges">
                <input type="color" data-option="fea-edge-colour" value={s.edgeColor} onChange={(e) => setEdgeColor(e.target.value)} className={swatch}/>
            </Row>
            <Row
                label={`FEA edge brightness ${s.edgeBrightness.toFixed(2)}`}
                title="The element edges' colour times this; 1.00 is the colour as chosen. The beam elements' grey edges dim with it."
            >
                <input
                    type="range"
                    data-option="fea-edge-brightness"
                    min={EDGE_BRIGHTNESS_RANGE.min}
                    max={EDGE_BRIGHTNESS_RANGE.max}
                    step={EDGE_BRIGHTNESS_RANGE.step}
                    value={s.edgeBrightness}
                    onChange={(e) => setEdgeBrightness(Number(e.target.value))}
                    className="w-28 cursor-pointer no-drag"
                />
            </Row>
            <div className="flex justify-end">
                <button
                    type="button"
                    data-option="fea-edge-reset"
                    onClick={resetEdges}
                    disabled={edgeLookIsDefault(s)}
                    className="text-xs text-blue-400 hover:text-blue-300 cursor-pointer disabled:opacity-50 disabled:cursor-default"
                    title={`FEA edges back to ${APPEARANCE_DEFAULTS.edgeColor}, brightness ${APPEARANCE_DEFAULTS.edgeBrightness.toFixed(2)}`}
                >
                    Default edges
                </button>
            </div>
            <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-gray-400">The same on every renderer.</span>
                <button
                    type="button"
                    data-option="appearance-reset"
                    onClick={reset}
                    disabled={appearanceIsDefault(s)}
                    className="text-blue-400 hover:text-blue-300 cursor-pointer disabled:opacity-50 disabled:cursor-default"
                    title={`Default colour ${APPEARANCE_DEFAULTS.defaultColor}, background ${APPEARANCE_DEFAULTS.background}, brightness ${APPEARANCE_DEFAULTS.brightness.toFixed(2)}, results shaded, FEA edges ${APPEARANCE_DEFAULTS.edgeColor}`}
                >
                    Reset
                </button>
            </div>
        </div>
    );
};

export default AppearanceOptions;
