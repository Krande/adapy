import {needsPreviewCompile, useCellBuilderStore, type CellBuilderMode} from "@/state/cellBuilderStore";
import {useScopeStore, scopeUrlPart} from "@/state/scopeStore";
import {viewerApi} from "@/services/viewerApi";
import {useExportPrefs, type ExportFormatId} from "./exportPrefs";
import {alertText, promptText} from "@/ui/confirm";

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

    const name = await promptText({
        title: "New procedural model",
        label: "Model name",
        placeholder: "e.g. Topside module A",
        confirmLabel: "Create",
    });
    if (!name) return;

    try {
        const detail = await viewerApi.createProceduralModel(scopeKey, name);
        useCellBuilderStore.getState().open(detail.id, detail.name, detail.revision, detail.doc);
    } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        void alertText({
            title: "Could not create the model",
            tone: "danger",
            body: [
                msg,
                // A bare 404 on this endpoint almost always means the backend does not
                // serve the procedural API — the local dev stub does not — and "404 Not
                // Found" on its own sends people looking for a typo in the name.
                /404/.test(msg)
                    ? "The server has no procedural-model API. In local development the REST stub only implements files and scopes."
                    : "Check that the server is reachable and that you have write access to this scope.",
            ],
        });
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

// ---- Builder view state ----------------------------------------------------
//
// These lived on the Builder panel's "View" tab, which was three unrelated things in one
// place: view state (which representation, superimpose, side-by-side, overlays), two
// actions, and two COMPILE settings that had nothing to do with viewing.
//
// The view state belongs in the View menu — "which of these am I looking at" and "show X
// on top of Y" are exactly what a View menu is for, and putting them there means they are
// findable without knowing the Builder panel has a tab called View. They grey out with a
// reason when no procedural model is open, like every other conditional command.

export type RepMode = "topology" | "simulation" | "detail";

export function setRepresentation(mode: RepMode): () => void {
    return () => void useCellBuilderStore.getState().setRepMode(mode);
}

export const representationIs = (mode: RepMode) => () =>
    useCellBuilderStore.getState().repMode === mode;

export function toggleSuperimpose(): void {
    const s = useCellBuilderStore.getState();
    void s.setSuperimpose(!s.superimpose);
}
export const superimposeOn = () => useCellBuilderStore.getState().superimpose;

export function toggleSideBySide(): void {
    const s = useCellBuilderStore.getState();
    s.setSideBySide(!s.sideBySide);
}
export const sideBySideOn = () => useCellBuilderStore.getState().sideBySide;

export function togglePortsOverlay(): void {
    const s = useCellBuilderStore.getState();
    s.setPortsOverlayVisible(!s.portsOverlayVisible);
}
export const portsOverlayOn = () => useCellBuilderStore.getState().portsOverlayVisible;

/** Recompute the model's placement so it sits centred after a far-off cell is removed. */
// ---------------------------------------------------------------------------
// Export, and the two catalogue analyses. These were the Builder panel's Tools tab: a
// row of five buttons, each of which does something and then leaves. Actions belong in
// the toolbar and the menus; what the Tools tab keeps is what those actions PRODUCE.
// ---------------------------------------------------------------------------

export interface ExportFormat {
    id: ExportFormatId;
    label: string;
    hint: string;
}

const ALL_FORMATS: ExportFormat[] = [
    {
        id: "xlsx",
        label: "Excel workbook",
        hint: "The current model as the engine's workbook. Edit it offline and import it back from Storage's + menu.",
    },
    {
        id: "ifc",
        label: "IFC (detail)",
        hint: "The DETAIL model: beams, plates, joints and equipment, with clash cuts as IfcRelVoidsElement voids.",
    },
    {
        id: "gxml",
        label: "Genie XML (simulation)",
        hint: "The SIMULATION model as a Genie concept XML (.gxml) for Sesam GeniE.",
    },
];

/**
 * The formats the current engine can actually produce.
 *
 * adapy-default is the only one that compiles a detail or simulation model; the rest
 * export the workbook only. Asking the store rather than hardcoding the list is what
 * stops a new engine silently offering downloads it cannot make — the panel did this
 * check inline, and it would have been easy to lose in the move.
 */
export function exportFormats(): ExportFormat[] {
    const engine = useCellBuilderStore.getState().selectedEngine || "adapy-default";
    return engine === "adapy-default" ? ALL_FORMATS : ALL_FORMATS.filter((f) => f.id === "xlsx");
}

/** The format the export button will produce, or null when none is chosen yet. */
export function chosenExportFormat(): ExportFormat | null {
    const id = useExportPrefs.getState().format;
    if (!id) return null;
    // A format the current engine cannot produce reads as nothing chosen: switching
    // engines must not leave the button claiming it will export an IFC it cannot make.
    return exportFormats().find((f) => f.id === id) ?? null;
}

export const exportFormatLabel = () => chosenExportFormat()?.label ?? null;

/** Export in the chosen format. Each of these commits unsaved edits first. */
export function runExport(): void {
    const fmt = chosenExportFormat();
    if (!fmt) return;
    const s = useCellBuilderStore.getState();
    if (fmt.id === "xlsx") void s.exportToExcel();
    else void s.exportModel(fmt.id);
}

/** Pick a format from the caret menu, and export in it straight away. */
export const pickExportFormat = (id: ExportFormatId) => () => {
    useExportPrefs.getState().setFormat(id);
    runExport();
};

export const needsExportable = () => {
    const s = useCellBuilderStore.getState();
    if (!s.active) return "No procedural model is open";
    if (s.xlsxBusy) return "An export is already running";
    return null;
};

/** Splice real catalogue CAD into the IFC instead of placeholder boxes. */
export const toggleIfcCad = () => {
    const s = useCellBuilderStore.getState();
    s.setExportIfcCad(!s.exportIfcCad);
};
export const ifcCadOn = () => useCellBuilderStore.getState().exportIfcCad;

/** Pull code archetype changes (new ports, corrected heights) into this scope's catalog. */
export const resyncEquipment = () => void useCellBuilderStore.getState().resyncEquipmentTypes();
export const needsResync = () => {
    const s = useCellBuilderStore.getState();
    if (!s.active) return "No procedural model is open";
    return s.resyncBusy ? "Already resyncing" : null;
};

/** Propose the fewest equipment moves that make cramped runs routable. Moves nothing. */
export const proposeRelocations = () => void useCellBuilderStore.getState().proposeRelocations();
export const needsRelocation = () => {
    const s = useCellBuilderStore.getState();
    if (!s.active) return "No procedural model is open";
    return s.relocationBusy ? "Already analysing" : null;
};

export function recentreModel(): void {
    useCellBuilderStore.getState().recenterModel();
}
