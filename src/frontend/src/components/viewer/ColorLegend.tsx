import React, {useMemo} from "react";

import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {getColormap} from "@/utils/scene/fea/colormaps";
import {
    bandedColormap,
    categoryEntries,
    contourBands,
    contourTicks,
} from "@/utils/scene/fea/contourScale";
import {visibleFieldValuesForSession} from "@/utils/scene/fea/visibleValues";
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
        resultColorsVisible,
        layer,
        ipReduction,
        colormap,
        contour,
    } = useFeaAnimationStore();

    const field = useMemo(
        () => manifest?.fields.find((candidate) => candidate.name_canonical === fieldName) ?? null,
        [manifest, fieldName],
    );
    const activeUnit = selectedResultUnit(field, reduction);
    const activeStep = field?.steps[stepIndex];
    const tickCount = Math.min(Math.max(step, 1), 6);
    const values = contourTicks([min, max], tickCount);

    // A categorical field — material, thickness, section — is a list of named
    // things, not a measurement, and a gradient from 1 to 7 tells a reader
    // nothing about which element is which. Its own names against its own
    // colours, restricted to what is on screen.
    const categories = useMemo(
        () =>
            sessionActive && field?.value_labels
                ? categoryEntries(
                      field.value_labels,
                      [min, max],
                      colormap,
                      visibleFieldValuesForSession(),
                  )
                : [],
        [sessionActive, field, min, max, colormap],
    );

    // Banded and continuous are the same legend drawn two ways from the same
    // numbers. Bands carry their own boundary values, so a reader can say which
    // interval an element is in rather than estimating it off a gradient — which
    // is the whole reason for asking for bands.
    const bands = useMemo(
        () =>
            sessionActive && contour.levels !== null
                ? contourBands([min, max], contour.levels, colormap)
                : null,
        [sessionActive, contour.levels, min, max, colormap],
    );

    const gradientStyle = useMemo(() => {
        if (!sessionActive) {
            const minColor = `rgb(${colorPalette[0].map((value) => value * 255).join(", ")})`;
            const maxColor = `rgb(${colorPalette[1].map((value) => value * 255).join(", ")})`;
            return {backgroundImage: `linear-gradient(to top, ${minColor}, ${maxColor})`};
        }
        const map = bandedColormap(getColormap(colormap), contour.levels);
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
    }, [sessionActive, colorPalette, colormap, contour.levels]);

    if (!showLegend) return null;

    const path = field?.group_path?.join(" / ") ?? field?.name_canonical;
    const selectableSurface = field?.surface === "selectable" || !!field?.surface_variants?.length;
    const surface = selectableSurface ? layer : field?.surface;
    const hasExactMarkers = field?.support === "result_point" || field?.support === "line_result_point";
    const pinned = contour.min !== null || contour.max !== null;

    // Panel tokens, not literal black-on-white. The legend floats over the scene
    // inside a themed shell, and a hard black slab reads as a foreign object
    // against it -- the capacity plugin's own scale beside it already used these,
    // so the two disagreed with each other.
    return (
        <div className="w-56 select-none rounded-sm border border-[var(--ada-panel-border)] bg-[var(--ada-surface-0)]/85 p-2 text-[11px] leading-tight text-[var(--ada-panel-text)] shadow-lg backdrop-blur-sm">
            {sessionActive && field && (
                <div className="mb-2 space-y-0.5 break-words">
                    <div className="font-semibold">{path}</div>
                    <div>{reduction}{activeUnit ? ` [${activeUnit}]` : ""}</div>
                    {/* Name and number both: the number is what the picker and the
                        oracle listings key on, the name is what the deck calls it. */}
                    {activeStep && (
                        <div>
                            Case: {activeStep.label}
                            {activeStep.name && activeStep.name !== activeStep.label
                                ? ` · ${activeStep.name}`
                                : ""}
                        </div>
                    )}
                    {surface && <div>Surface/layer: {surface}</div>}
                    {field.coordinate_system && <div>Axes: {field.coordinate_system}</div>}
                    {hasExactMarkers && <div>Markers: exact · contour: {ipReduction} reduction</div>}
                    {/* A pinned scale is invisible in the picture, and a reader who
                        does not know the range was fixed reads the colours as the
                        field's own extremes. Say so. */}
                    {pinned && <div className="opacity-80">Range: set by hand</div>}
                </div>
            )}
            {/* With result colouring switched off the scale would describe a
                painting that is not on screen, and a reader has no way to tell a
                stale legend from a live one. Say so instead: the field, step and
                range above are all still true, and this is the one line that is
                not. */}
            {!resultColorsVisible ? (
                <div className="rounded-sm border border-dashed border-[var(--ada-panel-border)] px-2 py-3 opacity-70">
                    Result colouring is off — the model is drawn in its base
                    material. Range {formatValue(min)} to {formatValue(max)}.
                </div>
            ) : categories.length > 0 ? (
                <ul className="flex flex-col gap-0.5">
                    {categories.map((entry) => (
                        <li key={entry.value} className="flex items-center gap-2">
                            <span
                                aria-hidden
                                className="h-3.5 w-3.5 shrink-0 rounded-[1px] border border-[var(--ada-panel-border)]"
                                style={{backgroundColor: entry.color}}
                            />
                            <span className="truncate" title={entry.label}>
                                {entry.label}
                            </span>
                        </li>
                    ))}
                </ul>
            ) : bands ? (
                <div className="flex flex-col-reverse font-mono tabular-nums">
                    {bands.map((band, index) => (
                        <div key={index} className="flex items-center gap-2">
                            <span
                                aria-hidden
                                className="h-5 w-5 shrink-0 rounded-[1px] border border-[var(--ada-panel-border)]"
                                style={{backgroundColor: band.color}}
                            />
                            <span title={`${formatValue(band.from)} … ${formatValue(band.to)}`}>
                                {formatValue(band.from)}
                            </span>
                        </div>
                    ))}
                    {/* The top boundary, which no band's own lower edge states. */}
                    <div className="flex items-center gap-2">
                        <span aria-hidden className="h-0 w-5 shrink-0" />
                        <span>{formatValue(bands[bands.length - 1].to)}</span>
                    </div>
                </div>
            ) : (
                <div className="flex h-64 gap-2">
                    <div className="w-5 shrink-0 rounded-sm" style={gradientStyle}/>
                    <div className="flex flex-1 flex-col justify-between font-mono tabular-nums">
                        {values.map((value, index) => (
                            <span key={index}>{formatValue(value)}</span>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};

export default ColorLegend;
