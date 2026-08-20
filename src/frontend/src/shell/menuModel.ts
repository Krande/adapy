import type {Command} from "./commandFilter";

// The application menu bar's structure, and the pure function that resolves it against
// the command registry.
//
// Why a menu bar at all, when there is a command palette and a tool rail:
//
//   * The palette answers "run the thing I can already name". It is useless for
//     discovery, because you cannot search for a word you have never seen.
//   * The tool rail changed contents per mode, so nothing had a fixed address and there
//     was no way to build a memory of where anything lived.
//
// A menu bar is the one durable, COMPLETE index: every command in the product, in a
// fixed place, in the same order every time. Items that cannot act right now are greyed
// with a reason rather than hidden — a menu whose contents move is a menu you cannot
// learn, which is the failure being corrected.
//
// This file is deliberately data + one pure function. It imports no store and no
// component, so the structure is testable under plain `node --test`, and a menu entry
// naming a command that does not exist is caught there rather than by someone clicking.

export type MenuItemDef =
    | {kind: "command"; id: string}
    | {kind: "separator"}
    | {kind: "submenu"; label: string; items: MenuItemDef[]};

export interface MenuDef {
    id: string;
    label: string;
    items: MenuItemDef[];
}

const cmd = (id: string): MenuItemDef => ({kind: "command", id});
const sep: MenuItemDef = {kind: "separator"};

/**
 * The menu set.
 *
 * Grouped by what the user is doing, not by which subsystem implements it — the
 * cellbuilder's compile and the converter's export both live under the menu you would
 * look in, not under a menu named after their code.
 */
export const MENUS: MenuDef[] = [
    {
        id: "file",
        label: "File",
        items: [
            cmd("action:upload"),
            cmd("action:convert"),
            cmd("action:refresh-files"),
            sep,
            cmd("panel:storage"),
            sep,
            // Settings under File follows the Windows/Linux convention (and PyCharm's).
            cmd("panel:preferences"),
        ],
    },
    {
        id: "edit",
        label: "Edit",
        items: [
            cmd("action:undo"),
            cmd("action:redo"),
            sep,
            cmd("action:copy-names"),
            sep,
            {
                kind: "submenu",
                label: "Select",
                items: [
                    cmd("action:select-parent"),
                    cmd("action:select-child"),
                    sep,
                    cmd("action:select-prev"),
                    cmd("action:select-next"),
                ],
            },
        ],
    },
    {
        id: "view",
        label: "View",
        items: [
            cmd("action:fit-all"),
            cmd("action:focus-selection"),
            sep,
            cmd("action:hide-selection"),
            cmd("action:unhide-all"),
            sep,
            cmd("panel:outliner"),
            cmd("panel:properties"),
            cmd("panel:scene"),
            sep,
            cmd("action:toggle-legend"),
            cmd("action:toggle-data-table"),
            cmd("action:fem-concepts"),
        ],
    },
    {
        id: "tools",
        label: "Tools",
        items: [
            cmd("action:compile-preview"),
            sep,
            cmd("panel:cellbuilder"),
            cmd("panel:node-editor"),
            sep,
            cmd("panel:simulation"),
            cmd("panel:fea-table"),
            sep,
            cmd("panel:convert"),
            cmd("panel:admin"),
        ],
    },
    {
        id: "window",
        label: "Window",
        items: [
            {
                kind: "submenu",
                label: "Mode",
                // Same order as the switcher, so the two never disagree about which comes first.
                items: [cmd("mode:data"), cmd("mode:build"), cmd("mode:inspect"), cmd("mode:results")],
            },
            sep,
            cmd("layout:reset-mode"),
            cmd("layout:reset-all"),
        ],
    },
    {
        id: "help",
        label: "Help",
        items: [cmd("help:shortcuts"), sep, cmd("help:about")],
    },
];

// ---------------------------------------------------------------------------

export type ResolvedItem =
    | {kind: "command"; command: Command}
    | {kind: "separator"}
    | {kind: "submenu"; label: string; items: ResolvedItem[]};

export interface ResolvedMenu {
    id: string;
    label: string;
    items: ResolvedItem[];
}

/**
 * Drop leading, trailing and repeated separators.
 *
 * Necessary because items disappear: `mode:inspect` is absent while you are already in
 * Inspect, and a deployment without REST has no admin panel. Without this, a menu shows
 * a rule against its top edge, or two rules with nothing between them, which reads as a
 * rendering bug rather than as "that item does not apply here".
 */
export function tidySeparators(items: ResolvedItem[]): ResolvedItem[] {
    const out: ResolvedItem[] = [];
    for (const it of items) {
        if (it.kind === "separator") {
            if (out.length === 0) continue;
            if (out[out.length - 1].kind === "separator") continue;
        }
        out.push(it);
    }
    while (out.length && out[out.length - 1].kind === "separator") out.pop();
    return out;
}

/**
 * Resolve the structure against the live command list.
 *
 * A named command that does not exist is skipped rather than rendered dead. Menus left
 * empty after resolution are dropped entirely — an empty menu title you can click and
 * get nothing from is worse than no title.
 */
export function resolveMenus(commands: Command[], defs: MenuDef[] = MENUS): ResolvedMenu[] {
    const byId = new Map(commands.map((c) => [c.id, c]));

    const resolve = (items: MenuItemDef[]): ResolvedItem[] =>
        tidySeparators(
            items.flatMap((it): ResolvedItem[] => {
                if (it.kind === "separator") return [{kind: "separator"}];
                if (it.kind === "submenu") {
                    const inner = resolve(it.items);
                    return inner.length ? [{kind: "submenu", label: it.label, items: inner}] : [];
                }
                const c = byId.get(it.id);
                return c ? [{kind: "command", command: c}] : [];
            }),
        );

    return defs
        .map((d) => ({id: d.id, label: d.label, items: resolve(d.items)}))
        .filter((m) => m.items.length > 0);
}

/** Every command id the menu structure names — used by the test that keeps the two in
 *  step, and to find commands reachable ONLY through the palette. */
export function menuCommandIds(defs: MenuDef[] = MENUS): string[] {
    const out: string[] = [];
    const walk = (items: MenuItemDef[]) => {
        for (const it of items) {
            if (it.kind === "command") out.push(it.id);
            else if (it.kind === "submenu") walk(it.items);
        }
    };
    defs.forEach((d) => walk(d.items));
    return out;
}
