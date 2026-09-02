import React, {useMemo} from "react";

import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {getColormap} from "@/utils/scene/fea/colormaps";
import {selectedResultUnit} from "@/utils/scene/fea/resultUnits";

function formatValue(value: number): string {
    if (!Number.isFinite(value)) return "—";
    const magnitude = Math.abs(value);
    if ((magnitude !== 0 && magnitude < 1e-3) || magnitude >= 1e6) {
        return value.toExponential(3);
    }
    return value.toLocaleString(undefined, {maximumSignificantDigits: 6});
}

const ColorLegend = () => {
    const {min, max, step, colorPalette, showLegend} = useColorStore();
    const {
        sessionActive,
        manifest,
        fieldName,
        reduction,
        stepIndex,
        layer,
        ipReduction,
        colormap,
    } = useFeaAnimationStore();

    const field = useMemo(
        () => manifest?.fields.find((candidate) => candidate.name_canonical === fieldName) ?? null,
        [manifest, fieldName],
    );
    const activeUnit = selectedResultUnit(field, reduction);
    const activeStep = field?.steps[stepIndex];
    const tickCount = Math.min(Math.max(step, 1), 6);
    const values = Array.from(
        {length: tickCount + 1},
        (_, index) => max - index * (max - min) / tickCount,
    );

    const gradientStyle = useMemo(() => {
        if (!sessionActive) {
            const minColor = `rgb(${colorPalette[0].map((value) => value * 255).join(", ")})`;
            const maxColor = `rgb(${colorPalette[1].map((value) => value * 255).join(", ")})`;
            return {backgroundImage: `linear-gradient(to top, ${minColor}, ${maxColor})`};
        }
        const map = getColormap(colormap);
        const rgb = new Float32Array(3);
        const stops: string[] = [];
        for (let index = 0; index <= 10; index++) {
            const position = index / 10;
            map(position, rgb);
            stops.push(
                `rgb(${Math.round(rgb[0] * 255)}, ${Math.round(rgb[1] * 255)}, ${Math.round(rgb[2] * 255)}) ${position * 100}%`,
            );
        }
        return {backgroundImage: `linear-gradient(to top, ${stops.join(", ")})`};
    }, [sessionActive, colorPalette, colormap]);

    if (!showLegend) return null;

    const path = field?.group_path?.join(" / ") ?? field?.name_canonical;
    const selectableSurface = field?.surface === "selectable" || !!field?.surface_variants?.length;
    const surface = selectableSurface ? layer : field?.surface;
    const hasExactMarkers = field?.support === "result_point" || field?.support === "line_result_point";

    return (
        <div className="w-56 rounded bg-black/75 p-2 text-[11px] leading-tight text-white shadow-lg">
            {sessionActive && field && (
                <div className="mb-2 space-y-0.5 break-words">
                    <div className="font-semibold">{path}</div>
                    <div>{reduction}{activeUnit ? ` [${activeUnit}]` : ""}</div>
                    {activeStep && <div>Case: {activeStep.label}</div>}
                    {surface && <div>Surface/layer: {surface}</div>}
                    {field.coordinate_system && <div>Axes: {field.coordinate_system}</div>}
                    {hasExactMarkers && <div>Markers: exact · contour: {ipReduction} reduction</div>}
                </div>
            )}
            <div className="flex h-64 gap-2">
                <div className="w-5 shrink-0 rounded-sm" style={gradientStyle}/>
                <div className="flex flex-1 flex-col justify-between font-mono tabular-nums">
                    {values.map((value, index) => (
                        <span key={index}>{formatValue(value)}</span>
                    ))}
                </div>
            </div>
        </div>
    );
};

export default ColorLegend;
