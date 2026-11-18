// Small presentational pieces of the Sources tab: banners, the shared control classes, icon
// buttons, the resolution-mode picker, the pending-revision chip and the orphan list.

import React from "react";
import type { ChangeState } from "@/assets/changes";
import { orphanHeading, orphanSentence, type OrphanEntry } from "@/assets/orphans";
import type { ResolutionMode } from "@/assets/types";
import { formatRevision } from "../format";

// Row-detail wording for the three non-`behind` states (`behind` gets its own
// sentence above, with the change itself). Kept as data so the four states'
// words are declared once rather than re-typed at each render.
export const CHANGE_STATE_LABEL: Record<ChangeState, string> = {
    behind: "behind — see the line above",
    current: "up to date — the change feed covered this root and found nothing newer",
    "not-recorded": "not recorded — the change feed has never covered this root",
    "no-feed": "unknown — this deployment has no change-feed database",
};

export const Banner: React.FC<{ tone: "info" | "warn" | "error"; children: React.ReactNode; title?: string }> = ({
    tone,
    children,
    title,
}) => (
    <div
        title={title}
        className={`mx-1 mt-1 rounded-sm px-2 py-1 text-xs ${
            tone === "error"
                ? "bg-red-900/60 text-red-100"
                : tone === "warn"
                  ? "bg-amber-900/50 text-amber-100"
                  : "bg-gray-700 text-gray-200"
        }`}
    >
        {children}
    </div>
);

/** The toolbar's one control surface: selects, the search field and icon buttons share it. */
export const CONTROL = "h-7 rounded-md border border-gray-700 bg-gray-800 text-xs text-gray-100";
/** Buttons in the detail block: one primary action, the rest secondary or quiet. */
export const BTN_PRIMARY =
    "h-7 px-3 rounded-md text-xs font-semibold bg-blue-400 text-gray-950 hover:bg-blue-300 disabled:opacity-50";
export const BTN_SECONDARY =
    "h-7 px-3 rounded-md text-xs font-medium border border-gray-700 bg-gray-800 text-gray-100 hover:bg-gray-700 disabled:opacity-50";
export const BTN_QUIET ="h-7 px-1.5 rounded-md text-xs text-gray-400 hover:text-white disabled:opacity-50";

/** A toolbar icon. `open`: the panel it toggles is showing -- the accent, so which panel is open
 *  reads at a glance. `pressed`: what it controls is in effect (a set narrowing the tree) with its
 *  panel shut -- a quieter mark. */
export const IconButton: React.FC<{ label: string; pressed?: boolean; open?: boolean; onClick: () => void; children: React.ReactNode }> = ({
    label,
    pressed,
    open,
    onClick,
    children,
}) => (
    <button
        type="button"
        aria-label={label}
        aria-pressed={pressed || open}
        aria-expanded={open}
        title={label}
        onClick={onClick}
        className={`h-7 w-7 shrink-0 grid place-items-center rounded-md border text-xs ${
            open
                ? "bg-blue-500/25 border-blue-400 text-blue-100"
                : pressed
                  ? "bg-gray-600 border-gray-500 text-white"
                  : "bg-gray-800 border-gray-700 text-gray-300 hover:text-white hover:bg-gray-700"
        }`}
    >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
            {children}
        </svg>
    </button>
);

export const ModePicker: React.FC<{ mode: ResolutionMode; revisions: readonly string[]; onChange: (m: ResolutionMode) => void }> = ({
    mode,
    revisions,
    onChange,
}) => {
    const newest = revisions[revisions.length - 1];
    return (
        <div className="flex items-center gap-1.5 min-w-0 shrink-0">
            <select
                aria-label="Resolution"
                className={`${CONTROL} px-2`}
                value={mode.kind}
                onChange={(e) => {
                    const kind = e.target.value as ResolutionMode["kind"];
                    if (kind === "latest") onChange({ kind });
                    else if (newest) onChange({ kind, revision: mode.kind === "latest" ? newest : mode.revision });
                }}
                title="latest: newest per subject (may mix publishes) · as of: newest at or before a revision · run: exactly one publish (coeval)"
            >
                <option value="latest">Latest</option>
                <option value="as-of" disabled={!newest}>As of</option>
                <option value="run" disabled={!newest}>Run</option>
            </select>
            {mode.kind !== "latest" && (
                <select
                    aria-label="Revision"
                    className={`${CONTROL} px-2 min-w-0 truncate`}
                    value={mode.revision}
                    onChange={(e) => onChange({ kind: mode.kind, revision: e.target.value })}
                >
                    {[...revisions].reverse().map((r) => (
                        <option key={r} value={r}>
                            {formatRevision(r)}
                        </option>
                    ))}
                </select>
            )}
        </div>
    );
};

/** Published subjects not placed yet because their branches are unopened: a neutral chip, with
 *  the action that turns "not placed yet" into a real answer. One level per unopened hierarchy,
 *  never a whole one -- once every hierarchy is open, what is still unplaced sits under a branch
 *  nobody has expanded, and expanding it is how it is placed. */
export const PendingChip: React.FC<{ pending: readonly string[]; unmergedSpines: number; loadingSpines: boolean; onPlace: () => void }> = ({
    pending,
    unmergedSpines,
    loadingSpines,
    onPlace,
}) => (
    <span
        className="inline-flex items-center gap-1 rounded-full bg-gray-700/70 px-2 py-0.5 text-gray-200"
        data-testid="asset-pending"
        title={`${pending.length} published subject(s) not placed yet -- their branches are unopened:\n${pending.join("\n")}`}
    >
        {pending.length} not placed
        {unmergedSpines > 0 && (
            <button
                type="button"
                className="text-blue-300 hover:text-white disabled:text-gray-500"
                disabled={loadingSpines}
                onClick={onPlace}
                title={`Open the first level of the ${unmergedSpines} unopened published hierarch${unmergedSpines === 1 ? "y" : "ies"} to place them`}
            >
                · {loadingSpines ? "placing…" : "place"}
            </button>
        )}
    </span>
);

/** Published subjects no loaded tree places, under the collection root.
 *  Collapsed to one line by default: the full sentence is on the entry's title
 *  and in the detail block when selected, so a long list cannot push the tree
 *  off a small screen. */
export const Orphans: React.FC<{
    orphans: readonly OrphanEntry[];
    pending: readonly string[];
    unmergedSpines: number;
    loadingSpines: boolean;
    selected: string | null;
    onSelect: (id: string) => void;
    onPlace: () => void;
}> = ({ orphans, pending, unmergedSpines, loadingSpines, selected, onSelect, onPlace }) => {
    const [open, setOpen] = React.useState(false);
    // Not orphans yet while some are pending: their branches are unopened. That is a chip in the
    // row under the search box (`PendingChip`), not a footer.
    if (pending.length || !orphans.length) return null;
    const ahead = orphans.filter((o) => o.cause === "ahead").length;
    const removed = orphans.length - ahead;
    return (
        <div className="border-t border-gray-700 text-xs shrink-0" data-testid="asset-orphans">
            <button
                type="button"
                className="w-full text-left px-2 py-0.5 text-amber-200 hover:bg-gray-700"
                onClick={() => setOpen((v) => !v)}
                aria-expanded={open}
                title="Published subjects that no loaded hierarchy places"
            >
                {open ? "▼" : "▶"} Not in this tree: {[ahead && `${ahead} ahead`, removed && `${removed} removed`].filter(Boolean).join(" · ")}
            </button>
            {open && (
                <div className="max-h-32 overflow-auto">
                    {(["ahead", "removed"] as const).map((cause) =>
                        orphans
                            .filter((o) => o.cause === cause)
                            .map((o) => (
                                <button
                                    key={o.id}
                                    type="button"
                                    onClick={() => onSelect(o.id)}
                                    className={`flex w-full items-center gap-1 text-left pl-5 pr-2 py-0.5 whitespace-nowrap ${
                                        selected === o.id ? "bg-blue-700" : "hover:bg-gray-700"
                                    }`}
                                    title={orphanSentence(o, formatRevision)}
                                >
                                    <span className="shrink-0 max-w-[55%] truncate text-white">{o.id}</span>
                                    <span className="min-w-0 truncate text-gray-400">
                                        {orphanHeading(o.cause).toLowerCase()} · {formatRevision(o.revision)}
                                    </span>
                                </button>
                            )),
                    )}
                </div>
            )}
        </div>
    );
};
