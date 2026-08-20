// The split-button rule: what the main half does, and what its tooltip says.
//
// Pure and separate from ModeToolbar, which imports the cellbuilder store and therefore
// the model worker — a test that reaches it dies on `?worker&inline`, which only a
// bundler resolves. Anything worth asserting starts life outside a component.

export interface TypeLike {
    slug: string;
    name: string;
    origin: string;
}

/**
 * The chosen type's label, or null when nothing is chosen yet.
 *
 * Null also when the chosen slug names a type that is no longer in the list — a model
 * can be reloaded with a different catalogue while a stale slug sits in the store, and
 * naming a type that no longer exists is worse than admitting none is chosen.
 */
export function chosenTypeLabel(
    types: TypeLike[],
    slug: string | null,
    labelOf: (t: TypeLike) => string,
): string | null {
    if (!slug) return null;
    const t = types.find((x) => x.slug === slug);
    return t ? labelOf(t) : null;
}

export interface SplitState {
    /** "run" fires the tool; "pick" opens the type menu instead. */
    action: "run" | "pick";
    tooltip: string;
}

/**
 * Given a tool's menu, its chosen type and whether it is already armed, decide what
 * pressing the main half does.
 *
 * Three cases, and the middle one is the whole point of the split button:
 *
 *   * No menu — it is an ordinary button.
 *   * A type is chosen — fire. Placing the tenth identical door should not cost a menu.
 *   * Nothing chosen — open the picker. Arming to place "nothing" is a press with no
 *     visible effect, which reads as a broken button.
 *
 * Already armed always fires, because a second press disarms: offering a type picker to
 * cancel something is answering a question nobody asked.
 */
export function splitButtonState(opts: {
    label: string;
    hasMenu: boolean;
    chosen: string | null;
    pressed: boolean;
}): SplitState {
    const {label, hasMenu, chosen, pressed} = opts;
    if (!hasMenu) return {action: "run", tooltip: label};
    if (pressed) return {action: "run", tooltip: chosen ? `${label}: ${chosen}` : label};
    if (chosen) return {action: "run", tooltip: `${label}: ${chosen}`};
    return {action: "pick", tooltip: `${label} — choose a type`};
}
