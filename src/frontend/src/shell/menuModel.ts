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
    | {kind: "submenu"; label: string; items: MenuItemDef[]}
    /**
     * Every command whose id starts with `prefix`, in the order the registry built them.
     *
     * For lists the menu structure cannot know in advance — saved workspaces are named by
     * the user, so there is no id to write down here. Naming a prefix instead keeps this
     * file what it is: a description of where things go, not a copy of what exists.
     */
    | {kind: "group"; prefix: string};

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
            // "New" first, as in every File menu ever written.
            cmd("action:new-procedural"),
            sep,
            cmd("action:upload"),
            cmd("action:convert"),
            cmd("action:refresh-files"),
            sep,
            cmd("panel:storage"),
            sep,
            // Settings under File follows the Windows/Linux convention (and PyCharm's).
            cmd("app:settings"),
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
            sep,
            {
                // From the Builder panel's old "View" tab. These are view state — which
                // representation you are looking at, what is drawn on top of what — and
                // a View menu is where you look for that. On the tab they were findable
                // only by knowing the Builder panel had a tab called View.
                kind: "submenu",
                label: "Builder",
                items: [
                    cmd("action:rep-topology"),
                    cmd("action:rep-simulation"),
                    cmd("action:rep-detail"),
                    sep,
                    cmd("action:superimpose"),
                    cmd("action:side-by-side"),
                    cmd("action:ports-overlay"),
                    sep,
                    cmd("action:recentre"),
                ],
            },
        ],
    },
    {
        id: "tools",
        label: "Tools",
        items: [
            cmd("action:compile-preview"),
            sep,
            // From the Builder panel's old Tools tab. Both analyse the model and report
            // back — occasional, deliberate, and named better in a menu than by an icon
            // nobody would recognise. The IFC option sits with them because it changes
            // what the export produces, not what you see.
            cmd("action:builder-resync"),
            cmd("action:builder-relocate"),
            cmd("action:builder-ifc-cad"),
            sep,
            cmd("panel:cellbuilder"),
            cmd("panel:component-build"),
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
                items: [cmd("mode:convert"), cmd("mode:build"), cmd("mode:inspect"), cmd("mode:results")],
            },
            sep,
            cmd("layout:customise-rail"),
            cmd("layout:save-workspace"),
            // Saved arrangements, one command each, named by the user. Nothing to list
            // here — the registry knows them and this says where they belong.
            {kind: "group", prefix: "layout:workspace:"},
            // Only exists once there is something to forget — the command is not
            // registered before that, and resolveMenus drops what it cannot find.
            cmd("layout:forget-workspace"),
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
                if (it.kind === "group") {
                    return commands
                        .filter((c) => c.id.startsWith(it.prefix))
                        .map((c) => ({kind: "command", command: c}) as ResolvedItem);
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
