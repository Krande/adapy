import {create} from "zustand";
import {TargetFormat} from "@/services/viewerApi";

// Per-row UI state for the dedicated /convert page. Job lifecycle
// (queued / progress / done / error) lives in `useConversionStore` —
// this store only tracks the page's own "what did the user just
// upload" list and the per-row target-format pick. Cleared on tab
// close; not persisted (scope already pins file ownership).

export interface ConvertRow {
    sourceKey: string;
    sizeBytes: number;
    addedAt: number;
    target: TargetFormat | null;
}

interface ConvertPageState {
    rows: ConvertRow[];
    addRow: (row: ConvertRow) => void;
    setTarget: (sourceKey: string, target: TargetFormat) => void;
    removeRow: (sourceKey: string) => void;
}

export const useConvertPageStore = create<ConvertPageState>((set) => ({
    rows: [],
    addRow: (row) =>
        set((s) => {
            const existing = s.rows.find((r) => r.sourceKey === row.sourceKey);
            if (existing) {
                return {
                    rows: s.rows.map((r) =>
                        r.sourceKey === row.sourceKey ? {...r, ...row} : r,
                    ),
                };
            }
            return {rows: [row, ...s.rows]};
        }),
    setTarget: (sourceKey, target) =>
        set((s) => ({
            rows: s.rows.map((r) =>
                r.sourceKey === sourceKey ? {...r, target} : r,
            ),
        })),
    removeRow: (sourceKey) =>
        set((s) => ({rows: s.rows.filter((r) => r.sourceKey !== sourceKey)})),
}));
