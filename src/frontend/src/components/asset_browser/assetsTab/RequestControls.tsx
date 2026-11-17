// Asking a provider for one node's (or a selection's) geometry: the picker and the controls.

import React, { useState } from "react";
import { createPortal } from "react-dom";
import { providerIdTitle } from "@/assets/providerNames";
import { useProviderName } from "@/state/providerNamesStore";
import type { NodeRequestControl } from "./loads";
import { BTN_PRIMARY, BTN_QUIET, BTN_SECONDARY } from "./chrome";

export const REQUEST_TITLE = "Ask a provider for this node's geometry, then publish it here";

/** Which providers to ask, as checkboxes -- the several providers that can be asked for one node
 *  each publish their own geometry for it, and asking all of them is rarely what is wanted. Opened
 *  from the detail's Request button or the row menu; portalled, like the menu, so it clears the
 *  drawer's overflow. */
export const RequestPicker: React.FC<{
    requests: readonly NodeRequestControl[];
    anchor: { x: number; y: number; above?: boolean };
    onClose: () => void;
}> = ({ requests, anchor, onClose }) => {
    const pn = useProviderName();
    const ref = React.useRef<HTMLDivElement>(null);
    const [picked, setPicked] = useState<ReadonlySet<string>>(
        () => new Set(requests.filter((r) => !r.blocked && !r.busy).map((r) => r.provider)),
    );
    const [pos, setPos] = useState<React.CSSProperties>({ left: anchor.x, top: anchor.y, visibility: "hidden" });
    React.useLayoutEffect(() => {
        const el = ref.current;
        const w = el?.offsetWidth ?? 240;
        const h = el?.offsetHeight ?? 160;
        const left = Math.max(8, Math.min(anchor.x, window.innerWidth - w - 8));
        const top = anchor.above ? Math.max(8, anchor.y - h - 6) : Math.max(8, Math.min(anchor.y, window.innerHeight - h - 8));
        setPos({ left, top });
        const outside = (e: Event) => {
            if (ref.current && !ref.current.contains(e.target as Node)) onClose();
        };
        const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
        document.addEventListener("mousedown", outside);
        document.addEventListener("keydown", onKey);
        return () => {
            document.removeEventListener("mousedown", outside);
            document.removeEventListener("keydown", onKey);
        };
    }, [anchor.x, anchor.y, anchor.above, onClose]);

    const toggle = (p: string) =>
        setPicked((cur) => {
            const next = new Set(cur);
            if (!next.delete(p)) next.add(p);
            return next;
        });
    const chosen = requests.filter((r) => picked.has(r.provider) && !r.blocked && !r.busy);
    return createPortal(
        <div
            ref={ref}
            role="dialog"
            aria-label="Request geometry"
            className="fixed z-[70] w-64 rounded-md border border-gray-700 bg-gray-800 shadow-lg text-xs text-gray-100"
            style={pos}
            onContextMenu={(e) => e.preventDefault()}
        >
            <div className="px-3 py-2 border-b border-gray-700 text-gray-400">Request geometry from</div>
            <div className="py-1">
                {requests.map((r) => (
                    <label
                        key={r.provider}
                        className={`flex items-start gap-2 px-3 py-1.5 ${r.blocked || r.busy ? "opacity-50" : "hover:bg-gray-700 cursor-pointer"}`}
                        title={`${r.blocked ?? (r.busy ? "Already requested; running" : REQUEST_TITLE)}\n${providerIdTitle(r.provider)}`}
                    >
                        <input
                            type="checkbox"
                            className="mt-0.5"
                            disabled={!!r.blocked || r.busy}
                            checked={picked.has(r.provider) && !r.blocked}
                            onChange={() => toggle(r.provider)}
                        />
                        <span className="min-w-0">
                            <span className="block font-medium truncate">{pn(r.provider)}</span>
                            <span className="block text-gray-400 truncate">{r.busy ? r.stage ?? "requesting…" : r.label}</span>
                        </span>
                    </label>
                ))}
            </div>
            <div className="flex justify-end gap-2 px-3 py-2 border-t border-gray-700">
                <button type="button" className={BTN_QUIET} onClick={onClose}>
                    Cancel
                </button>
                <button
                    type="button"
                    className={BTN_PRIMARY}
                    disabled={!chosen.length}
                    onClick={() => {
                        for (const r of chosen) r.run();
                        onClose();
                    }}
                >
                    Request{chosen.length > 1 ? ` (${chosen.length})` : ""}
                </button>
            </div>
        </div>,
        document.body,
    );
};

/** The request button and each running request's progress, for the detail's bottom row. One
 *  provider: the button asks it directly. Several: it opens the picker. */
export const RequestControls: React.FC<{ requests: readonly NodeRequestControl[] }> = ({ requests }) => {
    const pn = useProviderName();
    const [pickerAt, setPickerAt] = useState<{ x: number; y: number; above: boolean } | null>(null);
    const single = requests.length === 1 ? requests[0] : null;
    const busy = requests.filter((r) => r.busy);
    const failed = requests.filter((r) => !r.busy && r.error);
    const allBlocked = requests.every((r) => !!r.blocked);
    return (
        <div className="flex items-center gap-2 min-w-0">
            <button
                type="button"
                className={`${BTN_SECONDARY} shrink-0`}
                disabled={single ? single.busy || !!single.blocked : allBlocked}
                title={single ? (single.blocked ?? REQUEST_TITLE) : REQUEST_TITLE}
                onClick={(e) => {
                    if (single) return single.run();
                    const r = e.currentTarget.getBoundingClientRect();
                    setPickerAt({ x: r.left, y: r.top, above: true });
                }}
            >
                {single ? (single.busy ? "Requesting…" : single.label) : "Request geometry…"}
            </button>
            {busy.length > 0 && (
                <span className="text-gray-400 truncate" title={busy.map((r) => `${pn(r.provider)}: ${r.stage ?? "requesting"}`).join("\n")}>
                    {busy.length === 1 ? `${pn(busy[0].provider)}: ${busy[0].stage ?? "requesting…"}` : `${busy.length} requests running`}
                </span>
            )}
            {busy.length === 0 && failed.length > 0 && (
                <span className="text-red-300 truncate" title={failed.map((r) => `${pn(r.provider)}: ${r.error}`).join("\n")}>
                    {failed.length === 1 && requests.length === 1 ? failed[0].error : `${failed.map((r) => pn(r.provider)).join(", ")} failed`}
                </span>
            )}
            {busy.length === 0 && failed.length === 0 && requests.some((r) => r.note) && (
                <span
                    className="text-gray-400 truncate"
                    title={requests
                        .filter((r) => r.note)
                        .map((r) => `${pn(r.provider)}: ${r.note}`)
                        .join("\n")}
                >
                    {requests
                        .filter((r) => r.note)
                        .map((r) => (requests.length > 1 ? `${pn(r.provider)} ${r.note}` : r.note))
                        .join("; ")}
                </span>
            )}
            {pickerAt && <RequestPicker requests={requests} anchor={pickerAt} onClose={() => setPickerAt(null)} />}
        </div>
    );
};
