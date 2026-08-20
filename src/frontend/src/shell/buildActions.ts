import {needsPreviewCompile, useCellBuilderStore, type CellBuilderMode} from "@/state/cellBuilderStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {viewerApi} from "@/services/viewerApi";

// Build-mode rail actions.
//
// Same discipline as inspectActions and resultsActions: every one delegates to the
// cellbuilder store's own action, so the rail is a second entry point and never a second
// implementation. The undo stack in particular must have exactly one owner —
// utils/cellbuilder/history.ts, via the store — or a rail undo and a keyboard undo will
// drift apart in ways that are very hard to reason about mid-edit.

export function undo(): void {
    useCellBuilderStore.getState().undo();
}

export function redo(): void {
    useCellBuilderStore.getState().redo();
}

/**
 * Run the preview compile — the same thing ⇧↵ and the panel's Compile button do.
 *
 * Gated on `needsPreviewCompile` for the same reason the button is: on an unchanged model
 * whose results are already in the scene it is a no-op, and firing a worker job for a
 * no-op is worse than a disabled control.
 */
export function compilePreview(): void {
    const s = useCellBuilderStore.getState();
    if (!s.active || !needsPreviewCompile(s)) return;
    void s.compilePreview();
}

/** Is there a procedural model open at all? Drives honest disabling in the rail. */
export function builderActive(): boolean {
    return useCellBuilderStore.getState().active !== null;
}

/**
 * Start a new procedural model.
 *
 * Extracted from StorageBrowser's "+" menu, which was its only home. Creating a model is
 * a File-menu operation in every application ever written, and the mode you do it FOR is
 * Build — so burying it in the file browser's plus menu meant the one place you would
 * look while in Build mode had no way to start.
 *
 * The Library keeps its entry too and now calls this, so there is one implementation
 * behind three doors rather than three implementations.
 */
export async function newProceduralModel(): Promise<void> {
    const scope = useScopeStore.getState().current;
    const scopeKey = scope ? scopeUrlPart(scope) : "user:me";

    // window.prompt is what the original used. Replacing it with a real dialog is worth
    // doing, but not in the same change that moves the action — a move and a rewrite in
    // one diff is how you lose track of which one broke something.
    const name = window.prompt("Name for the new procedural model:", "");
    if (!name || !name.trim()) return;

    try {
        const detail = await viewerApi.createProceduralModel(scopeKey, name.trim());
        useCellBuilderStore.getState().open(detail.id, detail.name, detail.revision, detail.doc);
    } catch (e) {
        window.alert(`Failed to create procedural model: ${e instanceof Error ? e.message : e}`);
    }
}

/**
 * Arm one of the placement modes — the same state the panel's "+ Cell" buttons set.
 *
 * Toggling: pressing the armed one disarms it, which is what Escape does and what a
 * pressed toolbar button should do.
 */
export function armAddMode(mode: Extract<CellBuilderMode, `add-${string}`>): () => void {
    return () => {
        const s = useCellBuilderStore.getState();
        s.setMode(s.mode === mode ? "idle" : mode);
    };
}

/** Is a given placement mode currently armed? Drives the toolbar's pressed state. */
export const addModeIs = (mode: string) => () => useCellBuilderStore.getState().mode === mode;

/** Add a loft member — a one-shot action, not a mode. */
export function addLoftMember(): void {
    useCellBuilderStore.getState().addLoftMember();
}
