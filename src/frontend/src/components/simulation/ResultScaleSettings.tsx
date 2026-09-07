import React, {useEffect, useState} from "react";

import {useColorStore} from "@/state/colorLegendStore";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {COLORMAP_NAMES} from "@/utils/scene/fea/colormaps";
import {MAX_LEVELS, MIN_LEVELS, contourBands} from "@/utils/scene/fea/contourScale";
import {applyContourSettings, resetContourBounds} from "@/utils/scene/fea/resultSelection";
import {selectedResultRange} from "@/utils/scene/fea/resultUnits";

// How the colour scale is drawn: the range it spans, how many bands it is cut
// into, and which ramp it uses.
//
// The three belong together because they are one decision. Picking a colormap
// without being able to fix the range gives you a picture that recolours itself
// on every load case; fixing a range without bands still leaves you estimating
// values off a gradient. Every post-processor puts them in one small dialog, and
// this is that dialog.
//
// It writes through ``applyContourSettings``, which repaints. A dialog that only
// set state would move the legend and leave the model as it was.

/** Parse a typed number, treating an empty box as "no override". */
function parseBound(raw: string): number | null {
    const trimmed = raw.trim();
    if (trimmed === "") return null;
    const value = Number(trimmed);
    return Number.isFinite(value) ? value : null;
}

/** The number as a box's value: empty when the bound is not pinned. */
function boundText(value: number | null): string {
    return value === null ? "" : String(value);
}

const ResultScaleSettings: React.FC<{onClose?: () => void}> = ({onClose}) => {
    const contour = useFeaAnimationStore((s) => s.contour);
    const colormap = useFeaAnimationStore((s) => s.colormap);
    const setColormap = useFeaAnimationStore((s) => s.setColormap);
    const manifest = useFeaAnimationStore((s) => s.manifest);
    const fieldName = useFeaAnimationStore((s) => s.fieldName);
    const reduction = useFeaAnimationStore((s) => s.reduction);
    const legendMin = useColorStore((s) => s.min);
    const legendMax = useColorStore((s) => s.max);

    // Local text state so a half-typed "-1e" does not repaint the model on every
    // keystroke, and so clearing a box does not immediately snap back to the
    // field's own number. Committed on blur and on Enter.
    const [minText, setMinText] = useState(boundText(contour.min));
    const [maxText, setMaxText] = useState(boundText(contour.max));
    useEffect(() => setMinText(boundText(contour.min)), [contour.min]);
    useEffect(() => setMaxText(boundText(contour.max)), [contour.max]);

    const field = manifest?.fields.find((f) => f.name_canonical === fieldName) ?? null;
    const auto = field ? selectedResultRange(field, reduction) : ([0, 1] as [number, number]);
    const banded = contour.levels !== null;

    const commitBounds = () => {
        void applyContourSettings({min: parseBound(minText), max: parseBound(maxText)});
    };

    const preview = contourBands([legendMin, legendMax], contour.levels ?? 10, colormap);

    return (
        <div className="flex w-64 flex-col gap-2 rounded-sm border border-[var(--ada-panel-border)] bg-[var(--ada-surface-0)] p-2 text-[11px] text-[var(--ada-panel-text)] shadow-lg">
            <div className="flex items-center justify-between">
                <span className="font-semibold uppercase tracking-wide">Result scale</span>
                {onClose && (
                    <button
                        type="button"
                        onClick={onClose}
                        className="px-1 opacity-70 hover:opacity-100"
                        aria-label="Close result scale settings"
                    >
                        ×
                    </button>
                )}
            </div>

            <label className="flex items-center justify-between gap-2">
                <span>Colours</span>
                <select
                    className="min-w-0 flex-1 rounded-sm border border-[var(--ada-panel-border)] bg-[var(--ada-surface-2,#fff)] px-1 py-0.5 text-[var(--ada-panel-text,#000)]"
                    value={colormap}
                    onChange={(e) => {
                        setColormap(e.target.value);
                        void applyContourSettings({});
                    }}
                >
                    {COLORMAP_NAMES.map((name) => (
                        <option key={name} value={name}>
                            {name}
                        </option>
                    ))}
                </select>
            </label>

            <label className="flex items-center justify-between gap-2">
                <span>Max</span>
                <input
                    type="text"
                    inputMode="decimal"
                    className="w-32 rounded-sm border border-[var(--ada-panel-border)] bg-[var(--ada-surface-2,#fff)] px-1 py-0.5 text-[var(--ada-panel-text,#000)]"
                    value={maxText}
                    placeholder={String(auto[1])}
                    onChange={(e) => setMaxText(e.target.value)}
                    onBlur={commitBounds}
                    onKeyDown={(e) => e.key === "Enter" && commitBounds()}
                    title="Upper end of the colour scale. Empty follows the field."
                />
            </label>
            <label className="flex items-center justify-between gap-2">
                <span>Min</span>
                <input
                    type="text"
                    inputMode="decimal"
                    className="w-32 rounded-sm border border-[var(--ada-panel-border)] bg-[var(--ada-surface-2,#fff)] px-1 py-0.5 text-[var(--ada-panel-text,#000)]"
                    value={minText}
                    placeholder={String(auto[0])}
                    onChange={(e) => setMinText(e.target.value)}
                    onBlur={commitBounds}
                    onKeyDown={(e) => e.key === "Enter" && commitBounds()}
                    title="Lower end of the colour scale. Empty follows the field."
                />
            </label>

            <button
                type="button"
                className="self-start rounded-sm border border-[var(--ada-panel-border)] px-2 py-0.5 hover:bg-[var(--ada-surface-2)]"
                onClick={() => void resetContourBounds()}
                title="Back to this field's own minimum and maximum"
            >
                Reset range
            </button>

            {/* Bands, as a switch and a count. Off is the continuous ramp the
                viewer has always drawn; a count is what makes the scale readable
                as intervals rather than as a gradient. */}
            <label className="flex items-center gap-2 pt-1">
                <input
                    type="checkbox"
                    checked={banded}
                    onChange={(e) => void applyContourSettings({levels: e.target.checked ? 9 : null})}
                />
                <span>Contour bands</span>
            </label>
            {banded && (
                <label className="flex items-center justify-between gap-2 pl-5">
                    <span>Levels</span>
                    <input
                        type="number"
                        min={MIN_LEVELS}
                        max={MAX_LEVELS}
                        step={1}
                        className="w-20 rounded-sm border border-[var(--ada-panel-border)] bg-[var(--ada-surface-2,#fff)] px-1 py-0.5 text-[var(--ada-panel-text,#000)]"
                        value={contour.levels ?? 9}
                        onChange={(e) => void applyContourSettings({levels: Number(e.target.value)})}
                        title={`How many bands the range is cut into (${MIN_LEVELS}–${MAX_LEVELS})`}
                    />
                </label>
            )}

            {/* What the choice looks like, before you close the dialog and find
                out. Same band colours the legend and the elements will use. */}
            <div className="flex h-3 overflow-hidden rounded-sm border border-[var(--ada-panel-border)]">
                {preview.map((band, index) => (
                    <span key={index} className="h-full flex-1" style={{backgroundColor: band.color}} />
                ))}
            </div>
        </div>
    );
};

export default ResultScaleSettings;
