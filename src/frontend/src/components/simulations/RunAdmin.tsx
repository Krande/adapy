// Managing runs in the runs list: picking many (a checkbox per run, shift-click for a range, a long
// press on a touch screen to start), the bar of what can be done to them, and the dialogs that
// move runs into a batch and delete a batch. The words and the selection itself are
// `@/services/simulations/runSelection`; the requests are the store's.

import React, { createContext, useContext, useRef, useState } from "react";

import { ModalButton, ModalFrame } from "@/components/ui/ModalFrame";
import { confirmDialog, errorText } from "@/plugins/dialogs";
import { isActiveRun, type SimulationBatch, type SimulationRun } from "@/services/api/simulations";
import type { ScopeUrl } from "@/services/api/client";
import {
  batchIdForLabel,
  bytesLabel,
  deleteBatchTitle,
  deleteRunsBody,
  deleteRunsTitle,
  hasFailures,
  resultSummary,
} from "@/services/simulations/runSelection";
import { useSimulationStore } from "@/state/simulationStore";

/** What a run's line needs to take part in a selection. */
export interface RunSelectApi {
  readonly selecting: boolean;
  isSelected(runId: string): boolean;
  /** A click on the run's checkbox (or its line, while selecting). */
  click(runId: string, shift: boolean): void;
  /** A long press on the run's line: start selecting with it. */
  longPress(runId: string): void;
  /** Say what an action did, under the list. */
  report(text: string, failed?: boolean): void;
}

const NONE: RunSelectApi = {
  selecting: false,
  isSelected: () => false,
  click: () => {},
  longPress: () => {},
  report: () => {},
};

export const RunSelectContext = createContext<RunSelectApi>(NONE);
export const useRunSelect = () => useContext(RunSelectContext);

/** How long a press is held before it is a long press. */
export const LONG_PRESS_MS = 500;

/** Pointer handlers that call `onLong` when the press is held, and swallow the click it ends in. */
export function useLongPress(onLong: () => void) {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const start = useRef<{ x: number; y: number } | null>(null);
  const fired = useRef(false);
  const clear = () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    start.current = null;
  };
  return {
    onPointerDown: (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      fired.current = false;
      start.current = { x: e.clientX, y: e.clientY };
      timer.current = setTimeout(() => {
        fired.current = true;
        timer.current = null;
        onLong();
      }, LONG_PRESS_MS);
    },
    onPointerMove: (e: React.PointerEvent) => {
      // A scroll is not a press.
      if (start.current && Math.hypot(e.clientX - start.current.x, e.clientY - start.current.y) > 10) clear();
    },
    onPointerUp: clear,
    onPointerLeave: clear,
    onPointerCancel: clear,
    onContextMenu: (e: React.MouseEvent) => {
      if (fired.current) e.preventDefault();
    },
    onClickCapture: (e: React.MouseEvent) => {
      if (fired.current) {
        fired.current = false;
        e.preventDefault();
        e.stopPropagation();
      }
    },
  };
}

const BAR_BUTTON =
  "rounded-sm bg-[var(--ada-panel-surface)] px-3 py-2 sm:py-0.5 text-[var(--ada-panel-text)] hover:bg-[color-mix(in_srgb,var(--ada-panel-text)_20%,transparent)] disabled:opacity-40";
const DANGER_BUTTON = `${BAR_BUTTON} text-[var(--ada-fail)]`;

/** Ask, then delete `runs`: the confirmation counts them and says their size when the server can. */
export async function confirmAndDeleteRuns(scope: ScopeUrl, runs: readonly SimulationRun[], report: RunSelectApi["report"]): Promise<boolean> {
  if (runs.length === 0) return false;
  const store = useSimulationStore.getState();
  const ids = runs.map((r) => r.run_id);
  let bytes: number | null = null;
  try {
    bytes = (await store.deleteRuns(scope, ids, { dry: true })).bytes;
  } catch {
    // Asked without the size.
  }
  const ok = await confirmDialog({
    title: deleteRunsTitle(ids.length, bytes),
    body: deleteRunsBody(ids.length, runs.filter(isActiveRun).length),
    tone: "danger",
    confirmLabel: ids.length === 1 ? "Delete run" : `Delete ${ids.length} runs`,
  });
  if (!ok) return false;
  try {
    const res = await store.deleteRuns(scope, ids);
    const size = bytesLabel(res.bytes);
    report(`${resultSummary(res, "deleted")}${size ? ` (${size} freed)` : ""}`, hasFailures(res));
    return true;
  } catch (err) {
    report(`Delete failed: ${errorText(err)}`, true);
    return false;
  }
}

/** The bar under the list while runs are being picked. */
export const SelectionBar: React.FC<{
  scope: ScopeUrl;
  selected: readonly SimulationRun[];
  /** How many are picked, including runs not loaded any more. */
  count: number;
  viewCount: number;
  allInView: boolean;
  onSelectAll: () => void;
  onDone: () => void;
  onMove: () => void;
  onActed: () => void;
}> = ({ scope, selected, count, viewCount, allInView, onSelectAll, onDone, onMove, onActed }) => {
  const { report } = useRunSelect();
  const removeFromBatch = useSimulationStore((s) => s.removeFromBatch);
  const [busy, setBusy] = useState(false);
  const inBatch = selected.filter((r) => r.batch?.id);

  const remove = async () => {
    setBusy(true);
    try {
      const byBatch = new Map<string, string[]>();
      for (const r of inBatch) byBatch.set(r.batch!.id, [...(byBatch.get(r.batch!.id) ?? []), r.run_id]);
      const all = [];
      for (const [batchId, ids] of byBatch) all.push(...(await removeFromBatch(scope, batchId, ids)).results);
      report(resultSummary({ results: all }, "removed"), hasFailures({ results: all }));
      onActed();
    } catch (err) {
      report(`Remove failed: ${errorText(err)}`, true);
    } finally {
      setBusy(false);
    }
  };

  const del = async () => {
    setBusy(true);
    try {
      if (await confirmAndDeleteRuns(scope, selected, report)) onActed();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="sticky bottom-0 z-10 flex flex-wrap items-center gap-1.5 border-t border-[var(--ada-panel-border)] bg-[var(--ada-panel-bg,var(--ada-panel-surface))] px-1 py-1.5 shadow-[0_-2px_6px_rgba(0,0,0,0.25)]"
      role="toolbar"
      aria-label="Selected runs"
      data-selection-bar
    >
      <span className="shrink-0 text-[var(--ada-panel-text)]" data-selection-count>
        {count} selected
      </span>
      <button type="button" className={BAR_BUTTON} disabled={viewCount === 0} onClick={onSelectAll} data-select-all>
        {allInView ? "Select none" : "Select all in view"}
      </button>
      <span className="flex-1" />
      <button type="button" className={BAR_BUTTON} disabled={busy || count === 0} onClick={onMove} data-action-move>
        Move to batch…
      </button>
      <button type="button" className={BAR_BUTTON} disabled={busy || inBatch.length === 0} onClick={() => void remove()} data-action-remove>
        Remove from batch
      </button>
      <button type="button" className={DANGER_BUTTON} disabled={busy || count === 0} onClick={() => void del()} data-action-delete>
        Delete
      </button>
      <button type="button" className={BAR_BUTTON} onClick={onDone} data-selection-done>
        Done
      </button>
    </div>
  );
};

const NEW_BATCH = "__new__";

/** Pick a batch the scope has, or name a new one, and move the selected runs into it. */
export const MoveToBatchDialog: React.FC<{
  scope: ScopeUrl;
  runs: readonly SimulationRun[];
  batches: readonly SimulationBatch[];
  onClose: () => void;
  onMoved: () => void;
}> = ({ scope, runs, batches, onClose, onMoved }) => {
  const moveRuns = useSimulationStore((s) => s.moveRuns);
  const { report } = useRunSelect();
  const [choice, setChoice] = useState<string>(batches[0]?.id ?? NEW_BATCH);
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const target = choice === NEW_BATCH ? null : choice;
  const elsewhere = runs.filter((r) => r.batch?.id && r.batch.id !== target).length;
  const running = runs.filter((r) => r.status === "running").length;
  const ready = !busy && runs.length > 0 && (target !== null || label.trim().length > 0);

  const move = async () => {
    setBusy(true);
    setError(null);
    try {
      const batchId = target ?? batchIdForLabel(label.trim());
      const res = await moveRuns(
        scope,
        batchId,
        runs.map((r) => r.run_id),
        { ...(target === null ? { label: label.trim() } : {}), replace: elsewhere > 0 },
      );
      report(`${resultSummary(res, "moved")} into "${res.batch.label}"`, hasFailures(res));
      onMoved();
      onClose();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  return (
    <ModalFrame
      title={`Move ${runs.length} run${runs.length === 1 ? "" : "s"} to a batch`}
      onClose={onClose}
      testId="move-to-batch"
      footer={
        <>
          <ModalButton onClick={onClose}>Cancel</ModalButton>
          <ModalButton primary disabled={!ready} onClick={() => void move()}>
            {busy ? "Moving…" : "Move"}
          </ModalButton>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1">
          <span className="text-gray-300">Batch</span>
          <select
            className="bg-gray-700 border border-gray-600 rounded-sm px-2 py-1 text-xs"
            value={choice}
            onChange={(e) => setChoice(e.target.value)}
            data-move-batch
          >
            {batches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.label} ({b.count} run{b.count === 1 ? "" : "s"})
              </option>
            ))}
            <option value={NEW_BATCH}>New batch…</option>
          </select>
        </label>
        {choice === NEW_BATCH && (
          <label className="flex flex-col gap-1">
            <span className="text-gray-300">Name of the new batch</span>
            <input
              type="text"
              value={label}
              maxLength={200}
              placeholder="e.g. Frame A welds"
              onChange={(e) => setLabel(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && ready) void move();
              }}
              className="bg-gray-700 border border-gray-600 rounded-sm px-2 py-1 text-xs"
              data-move-label
            />
          </label>
        )}
        {elsewhere > 0 && (
          <p className="text-gray-300">
            {elsewhere} of them {elsewhere === 1 ? "is" : "are"} in another batch and will be moved out of it.
          </p>
        )}
        {running > 0 && (
          <p className="text-yellow-300">
            {running} {running === 1 ? "is" : "are"} running and will be left where {running === 1 ? "it is" : "they are"}: a
            run is moved once it has ended.
          </p>
        )}
        {error && <p className="text-red-300 break-words">{error}</p>}
      </div>
    </ModalFrame>
  );
};

/** Delete a batch: keep its runs (back among the runs of no batch) or delete them too. */
export const DeleteBatchDialog: React.FC<{
  scope: ScopeUrl;
  batch: SimulationBatch;
  onClose: () => void;
}> = ({ scope, batch, onClose }) => {
  const deleteBatch = useSimulationStore((s) => s.deleteBatch);
  const { report } = useRunSelect();
  const [withRuns, setWithRuns] = useState(false);
  const [bytes, setBytes] = useState<number | null | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // The size of what would go, asked for once the runs are to be deleted too.
  const chooseRuns = (value: boolean) => {
    setWithRuns(value);
    if (value && bytes === undefined) {
      setBytes(null);
      deleteBatch(scope, batch.id, { runs: true, dry: true })
        .then((res) => setBytes(res.bytes))
        .catch(() => setBytes(null));
    }
  };

  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await deleteBatch(scope, batch.id, { runs: withRuns });
      const what = resultSummary(res, withRuns ? "deleted" : "removed");
      report(
        res.batch.deleted ? `Batch "${batch.label}" deleted · ${what}` : `Batch "${batch.label}" kept: ${what}`,
        !res.batch.deleted || hasFailures(res),
      );
      onClose();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };

  const size = bytesLabel(bytes ?? null);
  return (
    <ModalFrame
      title={deleteBatchTitle(batch.label, batch.count)}
      onClose={onClose}
      testId="delete-batch"
      footer={
        <>
          <ModalButton onClick={onClose}>Cancel</ModalButton>
          <button
            type="button"
            disabled={busy}
            onClick={() => void go()}
            className="px-3 py-1 rounded-sm text-xs text-white bg-red-700 hover:bg-red-600 disabled:opacity-50"
            data-delete-batch-confirm
          >
            {busy ? "Deleting…" : withRuns ? `Delete batch and ${batch.count} run${batch.count === 1 ? "" : "s"}` : "Delete batch"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-2" role="radiogroup" aria-label="What happens to its runs">
        <label className="flex items-start gap-2">
          <input type="radio" name="batch-runs" checked={!withRuns} onChange={() => chooseRuns(false)} data-keep-runs />
          <span>
            Keep runs <span className="text-gray-400">(move them to the root of the list)</span>
          </span>
        </label>
        <label className="flex items-start gap-2">
          <input type="radio" name="batch-runs" checked={withRuns} onChange={() => chooseRuns(true)} data-delete-runs />
          <span>
            Delete runs too{" "}
            <span className="text-gray-400">
              ({batch.count} run{batch.count === 1 ? "" : "s"}
              {size ? `, ${size}` : bytes === null && withRuns ? ", sizing…" : ""}; this cannot be undone)
            </span>
          </span>
        </label>
        {batch.active > 0 && (
          <p className="text-yellow-300">
            {batch.active} of its runs {batch.active === 1 ? "is" : "are"} still going:{" "}
            {withRuns ? "they are cancelled first." : "a running run stays in the batch until it has ended."}
          </p>
        )}
        {error && <p className="text-red-300 break-words">{error}</p>}
      </div>
    </ModalFrame>
  );
};
