// The legend of a colour-by that core's colour legend does not draw itself (the
// models, a registered source). An FEA property is drawn by `ColorLegend`.

import React from "react";

import {useColorByStore} from "@/state/colorByStore";

export function ColorByLegend() {
    const legend = useColorByStore((s) => s.legend);
    if (!legend || !legend.entries.length) return null;
    return (
        <div
            className="max-h-72 w-56 select-none overflow-auto rounded-sm border border-[var(--ada-panel-border)] bg-[var(--ada-panel-bg)] p-2 text-[11px] leading-tight text-[var(--ada-panel-text)] shadow-lg backdrop-blur-sm"
            data-color-by-legend=""
        >
            <div className="mb-1 font-semibold">{legend.title}</div>
            <ul className="flex flex-col gap-0.5">
                {legend.entries.map((e) => (
                    <li key={e.label} className="flex items-center gap-2">
                        <span aria-hidden className="h-3.5 w-3.5 shrink-0 rounded-[1px] border border-[var(--ada-panel-border)]" style={{backgroundColor: e.color}} />
                        <span className="truncate" title={e.label}>
                            {e.label}
                        </span>
                    </li>
                ))}
            </ul>
        </div>
    );
}
