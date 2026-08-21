import React from "react";
import {create} from "zustand";
import {Button, Checkbox, Dialog, EmptyState} from "@/components/ui";
import {Icon, type IconName} from "@/components/icons";
import {canHide, customisableTools} from "./railArrangement";
import {useRailPrefs} from "./railPrefs";

// The rail's customise list.
//
// Its own tiny store rather than a prop, for the same reason the Settings dialog has one:
// it is opened from a right-click menu inside the rail AND from the View menu, and
// threading an `open` boolean from a shell-level parent through both would put the
// dialog's state further from the dialog than either caller is.

interface RailCustomiseState {
    open: boolean;
    setOpen: (open: boolean) => void;
}

const useRailCustomise = create<RailCustomiseState>((set) => ({
    open: false,
    setOpen: (open) => set({open}),
}));

export const openRailCustomise = () => useRailCustomise.getState().setOpen(true);

/** What the dialog needs to know about a tool. Supplied by ToolRail, which owns the list. */
export interface RailToolInfo {
    id: string;
    icon: IconName;
    label: string;
    divider?: boolean;
    essential?: boolean;
    shortcut?: string;
}

let TOOLS: RailToolInfo[] = [];

/**
 * Hand the dialog the rail's tool list.
 *
 * ToolRail calls this at module scope. The alternative — importing RAIL_TOOLS here —
 * makes ToolRail and this file import each other, and the cycle resolves differently in
 * the three builds. This direction has one owner and no cycle.
 */
export function registerRailTools(tools: RailToolInfo[]): void {
    TOOLS = tools;
}

export default function RailCustomiseDialog() {
    const open = useRailCustomise((s) => s.open);
    const setOpen = useRailCustomise((s) => s.setOpen);
    const hidden = useRailPrefs((s) => s.hidden);
    const toggleHidden = useRailPrefs((s) => s.toggleHidden);
    const reset = useRailPrefs((s) => s.reset);

    if (!open) return null;
    const tools = customisableTools(TOOLS);

    return (
        <Dialog
            open
            onClose={() => setOpen(false)}
            title="Customise the tool rail"
            footer={
                <>
                    <Button variant="subtle" onClick={reset} disabled={hidden.length === 0}>
                        Show every tool
                    </Button>
                    <Button variant="primary" onClick={() => setOpen(false)}>
                        Done
                    </Button>
                </>
            }
        >
            <div className="flex flex-col gap-3">
                <p className="text-xs text-content-subtle">
                    Choose which tools the rail offers. The order and the grouping stay put — a tool
                    means the same thing in every mode and sits in the same place, which is the whole
                    point of the rail.
                </p>
                {tools.length === 0 ? (
                    <EmptyState title="No rail tools registered" centered={false} />
                ) : (
                    <ul className="flex flex-col">
                        {tools.map((t) => {
                            const isHidden = hidden.includes(t.id);
                            const allowed = canHide(TOOLS, hidden, t.id);
                            return (
                                <li key={t.id}>
                                    <Checkbox
                                        checked={!isHidden}
                                        disabled={!allowed}
                                        onChange={() => toggleHidden(t.id)}
                                        label={
                                            <span className="flex w-full min-w-0 items-center gap-2">
                                                <Icon name={t.icon} size="sm" className="shrink-0 opacity-70" />
                                                <span className="truncate">{t.label.split(" — ")[0]}</span>
                                                {t.shortcut && (
                                                    <span className="ml-auto shrink-0 text-[11px] text-content-subtle">
                                                        {t.shortcut}
                                                    </span>
                                                )}
                                            </span>
                                        }
                                    />
                                </li>
                            );
                        })}
                    </ul>
                )}
                {/* Says why the last checkbox stopped responding, rather than leaving it
                    looking broken. */}
                {tools.filter((t) => !hidden.includes(t.id)).length <= 1 && (
                    <p className="text-xs text-content-muted">
                        The last tool cannot be hidden — an empty rail is indistinguishable from a
                        broken one.
                    </p>
                )}
            </div>
        </Dialog>
    );
}
