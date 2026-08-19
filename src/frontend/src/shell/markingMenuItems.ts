import type {IconName} from "@/components/icons";
import type {ModeId} from "./modeStore";

// What a marking menu offers, given what you right-clicked and where you are.
//
// PURE — no stores, no React. The wiring half (markingMenu.ts) supplies the context and
// the handlers; keeping the choice of items here means the rules are testable under plain
// `node --test`, and the rules are the part with judgement in them.
//
// Maya's marking menus work because the item in a given direction is ALWAYS the same
// item: you learn "right-click, flick left, release" as one gesture and stop reading the
// menu. That only holds if positions are stable, so directions are assigned explicitly
// per context rather than by filling slots in order.

/** Compass positions, clockwise from north. */
export const DIRECTIONS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"] as const;
export type Direction = (typeof DIRECTIONS)[number];

export interface MarkingItem {
    id: string;
    label: string;
    icon: IconName;
    dir: Direction;
    /** Rendered dimmed and unclickable, with the reason as its tooltip. */
    disabledReason?: string;
}

export interface MarkingContext {
    mode: ModeId;
    /** What the right-click landed on. */
    target: "geometry" | "empty";
    /** Something is selected (not necessarily what was clicked). */
    hasSelection: boolean;
    /** The scene holds anything at all. */
    hasEntities: boolean;
    /** A FEA result session is live — enables the result-specific entries. */
    feaActive: boolean;
    /** A procedural model is open. */
    builderActive: boolean;
}

/**
 * Items for a context.
 *
 * Two rules, both about muscle memory:
 *
 *   1. A given action keeps its direction across contexts. Hide is always W, Fit is
 *      always N. Moving an item because a different one is unavailable is what makes a
 *      radial menu unlearnable.
 *   2. An action that does not apply is DISABLED IN PLACE, not removed — for the same
 *      reason. The gesture still lands where you expect, and the tooltip says why it did
 *      nothing.
 */
export function markingItemsFor(ctx: MarkingContext): MarkingItem[] {
    const items: MarkingItem[] = [];

    // ---- N: frame. Always available while anything is loaded. ----
    items.push({
        id: "fit-all",
        label: "Fit all",
        icon: "expand",
        dir: "N",
        disabledReason: ctx.hasEntities ? undefined : "Nothing is loaded",
    });

    // ---- NE: focus the selection. ----
    items.push({
        id: "focus-selection",
        label: "Focus selection",
        icon: "mode-inspect",
        dir: "NE",
        disabledReason: ctx.hasSelection ? undefined : "Nothing is selected",
    });

    // ---- E: inspect. Opens Properties on what you clicked. ----
    items.push({
        id: "show-properties",
        label: "Properties",
        icon: "info",
        dir: "E",
    });

    // ---- SE: mode-specific slot. ----
    if (ctx.mode === "results") {
        items.push({
            id: "show-in-data",
            label: "Show in data",
            icon: "fem-data",
            dir: "SE",
            disabledReason: ctx.feaActive ? undefined : "No result session",
        });
    } else if (ctx.mode === "build") {
        items.push({
            id: "compile-preview",
            label: "Compile preview",
            icon: "reload",
            dir: "SE",
            disabledReason: ctx.builderActive ? undefined : "No procedural model open",
        });
    } else {
        items.push({id: "section-planes", label: "Section planes", icon: "section-plane", dir: "SE"});
    }

    // ---- S: copy. Cheap, frequent, and otherwise only on Shift+C. ----
    items.push({
        id: "copy-names",
        label: "Copy name",
        icon: "copy",
        dir: "S",
        disabledReason: ctx.hasSelection ? undefined : "Nothing is selected",
    });

    // ---- SW: undo, where the builder makes it constant. ----
    items.push({
        id: "undo",
        label: "Undo",
        icon: "undo",
        dir: "SW",
        disabledReason: ctx.builderActive ? undefined : "Only while editing a procedural model",
    });

    // ---- W: hide. The single most-used viewport verb. ----
    items.push({
        id: "hide-selection",
        label: "Hide",
        icon: "view-off",
        dir: "W",
        disabledReason: ctx.hasSelection ? undefined : "Nothing is selected",
    });

    // ---- NW: unhide. Deliberately opposite Hide — the pair reads as one axis. ----
    items.push({
        id: "unhide-all",
        label: "Unhide all",
        icon: "view",
        dir: "NW",
        disabledReason: ctx.hasEntities ? undefined : "Nothing is loaded",
    });

    return items;
}

/**
 * The direction a drag points, or null when it has not travelled far enough.
 *
 * The dead zone is what lets a plain right-click-and-release open the menu instead of
 * immediately picking whatever the cursor twitched towards.
 */
export function directionFromDelta(dx: number, dy: number, deadZone = 24): Direction | null {
    if (Math.hypot(dx, dy) < deadZone) return null;
    // Screen y grows downward; negate so N is up.
    const deg = (Math.atan2(-dy, dx) * 180) / Math.PI;
    // Rotate so 0° is north, then bucket into 45° sectors.
    const fromNorth = (450 - deg) % 360;
    return DIRECTIONS[Math.round(fromNorth / 45) % 8];
}
