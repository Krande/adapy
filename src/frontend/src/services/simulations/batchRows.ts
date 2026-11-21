// What a batch's line in the runs list says, the order of the lines, and which batches are open:
// no React, so it is tested alone.
//
// A batch is the runs a client started together (`SimulationBatch`): the list shows it as one
// line -- label, provider, how many runs and in which status, how far along, when the newest was
// started -- that opens to its runs. The runs of no batch are lines of their own, as before; both
// kinds are ordered by when they were (last) started, newest first.

import type { SimulationBatch, SimulationRun, SimulationRunStatus } from "@/services/api/simulations";

/** The statuses a batch's line counts, in the order it says them, and what it calls each. */
export const BATCH_STATUS_ORDER: ReadonlyArray<{ status: SimulationRunStatus; label: string }> = [
  { status: "done", label: "done" },
  { status: "running", label: "running" },
  { status: "queued", label: "queued" },
  { status: "error", label: "failed" },
  { status: "cancelled", label: "cancelled" },
];

/** The statuses a batch's runs are listed in while its cancelled runs are hidden. */
export const NOT_CANCELLED: readonly SimulationRunStatus[] = ["queued", "running", "done", "error"];

/** "86 done · 4 running · 300 queued": each status the batch has runs in, in a fixed order. */
export function batchCountsLabel(batch: Pick<SimulationBatch, "counts">, opts: { cancelled?: boolean } = {}): string {
  return BATCH_STATUS_ORDER.filter(({ status }) => (opts.cancelled ?? true) || status !== "cancelled")
    .map(({ status, label }) => ({ n: batch.counts?.[status] ?? 0, label }))
    .filter(({ n }) => n > 0)
    .map(({ n, label }) => `${n} ${label}`)
    .join(" · ");
}

/** How a batch's line is coloured: running while any run is going, an error while any failed, else
 *  done -- or cancelled when every run was. */
export function batchTone(batch: Pick<SimulationBatch, "counts" | "count" | "active">): string {
  if (batch.active > 0) return (batch.counts?.running ?? 0) > 0 ? "running" : "queued";
  if ((batch.counts?.error ?? 0) > 0) return "attention";
  if (batch.count > 0 && (batch.counts?.cancelled ?? 0) === batch.count) return "cancelled";
  return "done";
}

/** "4 %" for a batch's progress bar and its title. */
export function batchPercent(batch: Pick<SimulationBatch, "progress">): number {
  return Math.round(Math.max(0, Math.min(1, Number(batch.progress) || 0)) * 100);
}

/** How many runs the list holds: those of no batch and every batch's. */
export function listedRunCount(runs: readonly unknown[], batches: ReadonlyArray<Pick<SimulationBatch, "count">>): number {
  return runs.length + batches.reduce((n, b) => n + b.count, 0);
}

export type RunListItem =
  | { readonly kind: "run"; readonly key: string; readonly at: string; readonly run: SimulationRun }
  | { readonly kind: "batch"; readonly key: string; readonly at: string; readonly batch: SimulationBatch };

/** The lines of the list: the batches and the runs of no batch, newest first. A run that names a
 *  batch the list has (a server that listed it anyway) is left to its batch. */
export function runListItems(runs: readonly SimulationRun[], batches: readonly SimulationBatch[]): RunListItem[] {
  const known = new Set(batches.map((b) => b.id));
  const items: RunListItem[] = [
    ...batches.map((batch) => ({ kind: "batch" as const, key: `batch:${batch.id}`, at: batch.newest_at ?? "", batch })),
    ...runs
      .filter((run) => !(run.batch && known.has(run.batch.id)))
      .map((run) => ({ kind: "run" as const, key: `run:${run.run_id}`, at: run.created_at ?? "", run })),
  ];
  // Stable: two lines started at the same moment keep the order they came in.
  return items
    .map((item, i) => ({ item, i }))
    .sort((a, b) => (a.item.at === b.item.at ? a.i - b.i : a.item.at < b.item.at ? 1 : -1))
    .map(({ item }) => item);
}

// ── which batches are open, for the session ─────────────────────────────────────────────────────

const OPEN_KEY = "ada.simulations.openBatches";
/** The tab's own copy, so a browser that keeps no session storage still remembers until a reload. */
let openHere: Set<string> | null = null;

const openId = (scope: string, batchId: string) => `${scope}|${batchId}`;

function loadOpen(): Set<string> {
  if (openHere) return openHere;
  openHere = new Set();
  try {
    const raw = globalThis.sessionStorage?.getItem(OPEN_KEY);
    const ids = raw ? (JSON.parse(raw) as unknown) : [];
    if (Array.isArray(ids)) for (const id of ids) if (typeof id === "string") openHere.add(id);
  } catch {
    // No session storage here (a private window, a test): the tab's copy is all there is.
  }
  return openHere;
}

/** Whether the batch's line is open: closed until it is opened, then as it was left, for the session. */
export function isBatchOpen(scope: string, batchId: string): boolean {
  return loadOpen().has(openId(scope, batchId));
}

export function setBatchOpen(scope: string, batchId: string, open: boolean): void {
  const ids = loadOpen();
  if (open) ids.add(openId(scope, batchId));
  else ids.delete(openId(scope, batchId));
  try {
    globalThis.sessionStorage?.setItem(OPEN_KEY, JSON.stringify([...ids]));
  } catch {
    // Kept for this tab only.
  }
}

/** Forget every open batch (tests). */
export function resetOpenBatches(): void {
  openHere = new Set();
  try {
    globalThis.sessionStorage?.removeItem(OPEN_KEY);
  } catch {
    // Nothing kept.
  }
}
