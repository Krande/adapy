import React, {useEffect, useRef, useState} from "react";

// Desktop storage table columns. ``width`` is the default px width; the
// user can drag the header borders to resize, and the choice persists in
// localStorage (see useResizableColumns). Derived products are no longer a
// column — they made every row tall and ate horizontal space; they now live
// in a per-row expandable overview (click the name chevron).
export const STORAGE_COLUMNS: {key: string; label: string; width: number}[] = [
    {key: "select", label: "", width: 40},
    {key: "name", label: "Name", width: 420},
    {key: "format", label: "Format", width: 128},
    {key: "size", label: "Size", width: 112},
    {key: "uploaded", label: "Uploaded", width: 176},
    {key: "actions", label: "", width: 256},
];

export const STORAGE_COL_WIDTHS_KEY = "ada-storage-col-widths";
export const MIN_COL_WIDTH = 48;

// Drag-to-resize column widths for a table-fixed table, persisted to
// localStorage. Returns the live widths (feed into <col> + table width)
// and a ``startResize(i)`` mousedown handler for the column's drag grip.
export function useResizableColumns(
    columns: {key: string; width: number}[],
    storageKey: string,
) {
    const [widths, setWidths] = useState<number[]>(() => {
        try {
            const raw = localStorage.getItem(storageKey);
            if (raw) {
                const arr = JSON.parse(raw);
                if (
                    Array.isArray(arr) &&
                    arr.length === columns.length &&
                    arr.every((n) => typeof n === "number" && n >= MIN_COL_WIDTH)
                ) {
                    return arr;
                }
            }
        } catch {
            /* corrupt entry — fall through to defaults */
        }
        return columns.map((c) => c.width);
    });

    const dragging = useRef<{index: number; startX: number; startW: number} | null>(null);

    useEffect(() => {
        const onMove = (e: MouseEvent) => {
            const d = dragging.current;
            if (!d) return;
            const next = Math.max(MIN_COL_WIDTH, d.startW + (e.clientX - d.startX));
            setWidths((w) => {
                if (w[d.index] === next) return w;
                const n = [...w];
                n[d.index] = next;
                return n;
            });
        };
        const onUp = () => {
            if (!dragging.current) return;
            dragging.current = null;
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
        return () => {
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
        };
    }, []);

    useEffect(() => {
        try {
            localStorage.setItem(storageKey, JSON.stringify(widths));
        } catch {
            /* storage full / blocked — resizing still works for the session */
        }
    }, [widths, storageKey]);

    const startResize = (index: number) => (e: React.MouseEvent) => {
        dragging.current = {index, startX: e.clientX, startW: widths[index]};
        // Lock the cursor + kill text selection for the whole drag, not
        // just while the pointer is over the 1.5px grip.
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";
        e.preventDefault();
        e.stopPropagation();
    };

    const total = widths.reduce((a, b) => a + b, 0);
    return {widths, startResize, total};
}
