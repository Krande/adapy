// The transport's keys, while a timeline is on screen: ← / → step back and on by the step size
// (Shift ×10, Alt ÷10), [ / ] go to the previous / next marker. They are listed in the shortcuts
// dialog (layout/coreShortcutTable.ts, "Playback") as keys of the transport's own, and taken here,
// on the window's capture phase, before the shortcut dispatcher: Shift+← / → select a tree sibling
// everywhere else, and step ten times further while something plays. Keys typed into a field stay
// the field's; a gallery walk keeps its plain ← / →, and the cell builder its arrows.

import {isTextField} from "@/layout/shortcutDispatch";
import {useGalleryStore} from "@/state/galleryStore";
import {builderCellsShown} from "@/state/cellbuilder/builderVisibility";
import type {ShortcutKeyEvent} from "@/layout/shortcuts";

export type PlaybackKeyAction =
    | {kind: "step"; direction: 1 | -1; shift: boolean; alt: boolean}
    | {kind: "marker"; direction: 1 | -1};

/** What a key press asks of the transport; null for a key that is not one of its own. */
export function playbackKeyAction(e: ShortcutKeyEvent): PlaybackKeyAction | null {
    if (e.ctrlKey || e.metaKey) return null;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        return {kind: "step", direction: e.key === "ArrowRight" ? 1 : -1, shift: e.shiftKey, alt: e.altKey};
    }
    if ((e.key === "[" || e.key === "]") && !e.altKey) return {kind: "marker", direction: e.key === "]" ? 1 : -1};
    return null;
}

/** Widgets that walk with the arrow keys themselves: a press while one has focus is theirs. */
const ARROW_WIDGETS =
    '[role="tree"],[role="treeitem"],[role="listbox"],[role="option"],[role="grid"],[role="gridcell"],' +
    '[role="menu"],[role="menuitem"],[role="tablist"],[role="tab"],[role="radiogroup"],[role="slider"],input[type="range"]';

type Focused = {closest?: (selector: string) => unknown};

/** Whether another widget keeps this press: a field being typed in, a widget that walks with the
 *  arrows (a tree, a list, another slider), a gallery walk's plain arrows, the cell builder's arrows.
 *  The transport's own bar is no other widget. */
export function keyBelongsElsewhere(e: ShortcutKeyEvent, action: PlaybackKeyAction): boolean {
    if (isTextField(e.target ?? null)) return true;
    if (action.kind !== "step") return false;
    const focused = e.target as Focused | null | undefined;
    if (typeof focused?.closest === "function" && !focused.closest("[data-playback-transport]") && focused.closest(ARROW_WIDGETS)) return true;
    if (!action.shift && !action.alt && useGalleryStore.getState().enabled) return true;
    return builderCellsShown() === true;
}

/** Take the transport's keys while `active()` holds; `run` does what they ask. Returns the undo. */
export function installPlaybackKeys(active: () => boolean, run: (action: PlaybackKeyAction) => void): () => void {
    if (typeof window === "undefined") return () => {};
    const onKey = (e: KeyboardEvent) => {
        if (e.defaultPrevented || !active()) return;
        const action = playbackKeyAction(e);
        if (!action || keyBelongsElsewhere(e, action)) return;
        e.preventDefault();
        e.stopPropagation();
        run(action);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
}
