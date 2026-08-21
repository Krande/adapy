// Which rail tools are shown, once the user has had their say.
//
// Show and hide, not reorder. The rail is grouped — camera, then visibility, then
// history — and the dividers are a claim about what belongs with what. Letting anyone
// drop Undo between Fit and Focus would break that claim while leaving the rules that
// draw it in place, so the arrangement stays and the contents are yours.
//
// That also keeps the promise the rail is built on: a tool means the same thing in every
// mode and sits in the same place. A rail you can shuffle is one where nobody's muscle
// memory transfers, including from a screenshot in the docs.
//
// Pure so the divider rules can be tested — they are the only part with any real
// reasoning in them, and they are invisible until they go wrong.

export interface RailItem {
    id: string;
    divider?: boolean;
    /** Not offered for hiding — see `alwaysShown`. */
    essential?: boolean;
}

/**
 * The rail as it should render.
 *
 * Hides what the user asked to hide, then tidies the dividers left behind: a rule against
 * the top edge, a rule against the bottom, or two rules in a row are all a rendering
 * fault as far as anyone looking at it is concerned.
 */
export function arrangeRail<T extends RailItem>(all: T[], hidden: readonly string[]): T[] {
    const hide = new Set(hidden);
    const kept = all.filter((t) => t.divider || !hide.has(t.id));

    const out: T[] = [];
    for (const t of kept) {
        if (!t.divider) {
            out.push(t);
            continue;
        }
        // A divider needs something before it, and it must not follow another.
        if (out.length === 0) continue;
        if (out[out.length - 1].divider) continue;
        out.push(t);
    }
    // And something after it.
    while (out.length && out[out.length - 1].divider) out.pop();
    return out;
}

/** The tools offered in the customise list, in rail order. Dividers are not choices. */
export function customisableTools<T extends RailItem>(all: T[]): T[] {
    return all.filter((t) => !t.divider && !t.essential);
}

/**
 * Hiding everything is not a choice anyone means to make.
 *
 * An empty rail is indistinguishable from a broken one, and the control that would let
 * you put it back is in a menu you now have no reason to think exists. So the last
 * visible tool cannot be hidden — the same reasoning as the outliner's never-filter-to-
 * nothing rule.
 */
export function canHide<T extends RailItem>(all: T[], hidden: readonly string[], id: string): boolean {
    if (hidden.includes(id)) return true; // unhiding is always fine
    const visible = customisableTools(all).filter((t) => !hidden.includes(t.id));
    return visible.length > 1;
}
