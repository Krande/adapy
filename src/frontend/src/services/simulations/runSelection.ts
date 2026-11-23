// Picking many runs in the runs list, and what the confirmations and results of acting on them say:
// no React, so it is tested alone.
//
// The selection is a set of run ids. A click toggles one and becomes the anchor; a shift-click
// selects every run between the anchor and the clicked one in the order the list shows them (the
// runs of no batch and those of the open batches, as laid out). "Select all in view" takes every run
// the list shows.

import type { RunAdminResponse, RunAdminResult } from "@/services/api/simulations";

export interface RunSelection {
  readonly ids: ReadonlySet<string>;
  /** The run the last plain click was on: where a shift-click range starts. */
  readonly anchor: string | null;
}

export const EMPTY_SELECTION: RunSelection = { ids: new Set(), anchor: null };

/** One run in or out; it becomes the anchor. */
export function toggleRun(sel: RunSelection, runId: string): RunSelection {
  const ids = new Set(sel.ids);
  if (ids.has(runId)) ids.delete(runId);
  else ids.add(runId);
  return { ids, anchor: runId };
}

/** Every run from the anchor to `runId` in `order` selected (added to what is selected). Without an
 *  anchor in `order` it is a plain toggle. The anchor stays. */
export function selectRange(sel: RunSelection, order: readonly string[], runId: string): RunSelection {
  const from = sel.anchor === null ? -1 : order.indexOf(sel.anchor);
  const to = order.indexOf(runId);
  if (from < 0 || to < 0) return toggleRun(sel, runId);
  const [lo, hi] = from <= to ? [from, to] : [to, from];
  const ids = new Set(sel.ids);
  for (const id of order.slice(lo, hi + 1)) ids.add(id);
  return { ids, anchor: sel.anchor };
}

/** A click on a run's checkbox: a range with shift, else a toggle. */
export function clickRun(sel: RunSelection, order: readonly string[], runId: string, shift: boolean): RunSelection {
  return shift ? selectRange(sel, order, runId) : toggleRun(sel, runId);
}

/** Every run in view selected; when every one already is, none. */
export function selectAllInView(sel: RunSelection, order: readonly string[]): RunSelection {
  const all = order.length > 0 && order.every((id) => sel.ids.has(id));
  if (all) {
    const ids = new Set(sel.ids);
    for (const id of order) ids.delete(id);
    return { ids, anchor: null };
  }
  return { ids: new Set([...sel.ids, ...order]), anchor: sel.anchor };
}

/** The selection without the runs that are gone (deleted), and without the ones no longer listed. */
export function pruneSelection(sel: RunSelection, keep: (runId: string) => boolean): RunSelection {
  const ids = new Set([...sel.ids].filter(keep));
  return { ids, anchor: sel.anchor !== null && ids.has(sel.anchor) ? sel.anchor : null };
}

// ── words ───────────────────────────────────────────────────────────────────────────────────────

/** "12 GB", "340 MB", "2.1 kB"; empty when unknown or nothing. */
export function bytesLabel(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes <= 0) return "";
  const units = ["B", "kB", "MB", "GB", "TB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${i === 0 ? n : n >= 10 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "Delete 37 runs (12 GB)?" -- the size left out when it is not known. */
export function deleteRunsTitle(count: number, bytes?: number | null): string {
  const size = bytesLabel(bytes);
  return `Delete ${plural(count, "run")}${size ? ` (${size})` : ""}?`;
}

/** What a run delete confirmation says beneath its title. */
export function deleteRunsBody(count: number, active: number): string[] {
  const out = [`${count === 1 ? "Its" : "Their"} record, artefacts and recordings are removed. This cannot be undone.`];
  if (active > 0) out.push(`${plural(active, "run is", "runs are")} still going and will be cancelled first.`);
  return out;
}

/** The title of a batch's delete dialog. */
export function deleteBatchTitle(label: string, count: number): string {
  return `Delete batch "${label}" (${plural(count, "run")})?`;
}

/** "Deleted 35 · 2 failed: it is running" -- what a bulk action did, the first reason of what did not. */
export function resultSummary(res: Pick<RunAdminResponse, "results">, done: "deleted" | "moved" | "removed"): string {
  const verb = { deleted: "Deleted", moved: "Moved", removed: "Removed" }[done];
  const ok = res.results.filter((r) => r.status === done && !r.already).length;
  const already = res.results.filter((r) => r.status === done && r.already).length;
  const unchanged = res.results.filter((r) => r.status === "unchanged").length;
  const bad = res.results.filter((r) => r.status === "failed" || r.status === "refused");
  const parts = [`${verb} ${ok}`];
  if (already) parts.push(`${already} already gone`);
  if (unchanged) parts.push(`${unchanged} unchanged`);
  if (bad.length) parts.push(`${bad.length} ${done === "deleted" ? "failed" : "refused"}: ${failureReasons(bad)}`);
  return parts.join(" · ");
}

/** The distinct reasons of the runs that failed, each with how many. */
export function failureReasons(bad: readonly RunAdminResult[]): string {
  const by = new Map<string, number>();
  for (const r of bad) {
    const reason = r.reason ?? r.code ?? "unknown";
    by.set(reason, (by.get(reason) ?? 0) + 1);
  }
  return [...by.entries()].map(([reason, n]) => (n > 1 ? `${reason} (${n})` : reason)).join("; ");
}

/** Whether a bulk action had any run it could not act on. */
export function hasFailures(res: Pick<RunAdminResponse, "results">): boolean {
  return res.results.some((r) => r.status === "failed" || r.status === "refused");
}

// ── a batch from a label ────────────────────────────────────────────────────────────────────────

const BATCH_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/** A batch id for a new batch the user named: the label as letters, digits and dashes, and a
 *  short stamp so two batches of the same name do not meet. */
export function batchIdForLabel(label: string, now: Date = new Date(), rand: () => number = Math.random): string {
  const slug =
    label
      .normalize("NFKD")
      .replace(/[^A-Za-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .toLowerCase()
      .slice(0, 80) || "batch";
  const p = (n: number) => String(n).padStart(2, "0");
  const stamp = `${now.getUTCFullYear()}${p(now.getUTCMonth() + 1)}${p(now.getUTCDate())}T${p(now.getUTCHours())}${p(now.getUTCMinutes())}`;
  const tail = Math.floor(rand() * 36 ** 4)
    .toString(36)
    .padStart(4, "0");
  const id = `${slug}-${stamp}-${tail}`;
  return BATCH_ID.test(id) ? id : `batch-${stamp}-${tail}`;
}
