/**
 * Shift+G: save the current selection as a named group in the current scope -- the keyboard path to
 * the Saved groups section's "Group selection…", over the same capture and store.
 */

import {useSavedGroupsStore} from "@/state/savedGroupsStore";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

import {captureSelection} from "./groupScene";

export async function groupSelectionFromKeyboard(): Promise<void> {
    const captured = captureSelection();
    if (captured.reason || captured.members.length === 0) {
        window.alert(captured.reason ?? "Nothing selected to group.");
        return;
    }
    const n = captured.members.length;
    const name = window.prompt(`Name for a group of ${n} member${n === 1 ? "" : "s"} (shared in this scope):`)?.trim();
    if (!name) return;
    const scope = scopeUrlPart(useScopeStore.getState().current);
    const saved = await useSavedGroupsStore.getState().create(scope, name, captured.members);
    if (!saved) window.alert(`Could not save the group "${name}" -- see the Saved groups section for why.`);
}
