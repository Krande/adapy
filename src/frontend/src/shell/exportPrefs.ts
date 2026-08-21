import {create} from "zustand";
import {persist} from "zustand/middleware";

// Which export format the Build toolbar's export button produces.
//
// A shell store rather than a field on cellBuilderStore, because cellBuilderStore is
// business logic and off limits to this rebuild — and because this genuinely is chrome
// state: it remembers which item of a split button you picked last, exactly as the
// opening and equipment type pickers do. The model does not care.

export type ExportFormatId = "xlsx" | "ifc" | "gxml";

interface ExportPrefsState {
    /** Null until you pick one — the button then opens the menu instead of guessing. */
    format: ExportFormatId | null;
    setFormat: (f: ExportFormatId) => void;
}

export const useExportPrefs = create<ExportPrefsState>()(
    persist(
        (set) => ({
            format: null,
            setFormat: (format) => set({format}),
        }),
        {name: "ada:export-prefs:v1"},
    ),
);
