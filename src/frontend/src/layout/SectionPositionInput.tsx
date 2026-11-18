// A section plane's position typed in, in model coordinates (utils/scene/sectionOffset): beside the
// slider on the section strip and in the classic Clip panel. It keeps what is being typed until Enter
// or leaving the field, and shows the plane's position again when the plane moves elsewhere.

import React from "react";

/** A position as the field shows it: to the millimetre, without trailing zeros. */
export const formatSectionPosition = (v: number): string => String(Math.round(v * 1000) / 1000 + 0);

export function SectionPositionInput({
    position,
    axisLabel,
    disabled,
    className,
    onCommit,
}: {
    position: number;
    /** "X", "Y" or "Z": the field's label. */
    axisLabel: string;
    disabled?: boolean;
    className?: string;
    onCommit(position: number): void;
}) {
    const shown = formatSectionPosition(position);
    const [text, setText] = React.useState(shown);
    // The plane moved (the slider, the gizmo, an undo): show where it is now.
    React.useEffect(() => setText(shown), [shown]);
    const commit = () => {
        const v = Number(text);
        if (text.trim() !== "" && Number.isFinite(v)) onCommit(v);
        else setText(shown);
    };
    return (
        <label className="flex items-center gap-1" title={`Section plane position on ${axisLabel}, in model coordinates`}>
            <span>{axisLabel} =</span>
            <input
                type="text"
                inputMode="decimal"
                className={className}
                value={text}
                disabled={disabled}
                aria-label={`Section plane position on ${axisLabel} (model coordinates)`}
                onChange={(e) => setText(e.target.value)}
                onBlur={commit}
                onKeyDown={(e) => {
                    if (e.key === "Enter") commit();
                    else if (e.key === "Escape") setText(shown);
                }}
            />
        </label>
    );
}
