/**
 * Clipboard helpers for the selected-object info panel and the
 * Shift+C keyboard shortcut. Single source of truth for "copy the
 * names of the currently-selected meshes to the clipboard".
 *
 * Both paths (kbd and tap-on-button) call into the same routines so
 * the navigator.clipboard / execCommand fallback chain stays in one
 * place — without that, mobile Safari (where the modern API is
 * sometimes locked behind a user gesture and sometimes fails
 * silently) and desktop Firefox give different behaviours, and one
 * UI gets fixed while the other regresses.
 */

import {queryNameFromRangeId} from "@/utils/mesh_select/queryMeshDrawRange";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

/** Resolve every (mesh, drawRangeId) pair in a selection map into
 * the corresponding element name via the existing range-id lookup.
 * Failed lookups are dropped silently. */
export async function resolveSelectionNames(
    selection: Map<CustomBatchedMesh | unknown, Set<string>>,
): Promise<string[]> {
    const lookups: Promise<string | null>[] = [];
    selection.forEach((rangeIds, mesh) => {
        const lookupKey: string | undefined =
            (mesh as any).unique_key ??
            ((mesh as any).userData ? (mesh as any).userData["unique_hash"] : undefined);
        if (!lookupKey) return;
        for (const rangeId of rangeIds) {
            lookups.push(queryNameFromRangeId(lookupKey, rangeId));
        }
    });
    const results = await Promise.allSettled(lookups);
    const names: string[] = [];
    for (const r of results) {
        if (r.status === "fulfilled" && r.value) names.push(r.value);
    }
    return names;
}

/** Best-effort write to the system clipboard. Returns true on
 * success. Tries the async navigator.clipboard API first, falls back
 * to a hidden-textarea + ``document.execCommand("copy")`` for
 * older / less-permissive contexts (notably mobile webviews where
 * the async path is blocked outside a fresh user gesture). */
export async function writeToClipboard(text: string): Promise<boolean> {
    if (text.length === 0) return false;
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return true;
        } catch {
            // fall through to legacy path
        }
    }
    if (typeof document === "undefined" || !document.body) return false;
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.width = "1px";
    ta.style.height = "1px";
    ta.style.opacity = "0";
    ta.setAttribute("readonly", "");
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    try {
        return document.execCommand("copy");
    } catch {
        return false;
    } finally {
        document.body.removeChild(ta);
    }
}

/** Convenience: resolve selection → join names with newlines →
 * copy. Returns the count of names actually copied (0 on failure). */
export async function copySelectionNames(
    selection: Map<CustomBatchedMesh | unknown, Set<string>>,
): Promise<number> {
    const names = await resolveSelectionNames(selection);
    if (names.length === 0) return 0;
    const ok = await writeToClipboard(names.join("\n"));
    return ok ? names.length : 0;
}
