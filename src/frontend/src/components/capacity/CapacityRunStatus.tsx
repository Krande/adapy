import React, { useState } from "react";

import type {
  CapacityCalcProgress,
  CapacityCalcRunProgress,
  CapacityCalcStep,
} from "@/state/capacityResultsStore";

/** Live status of a `--stream-results` code check.
 *
 *  The calculation runs for the better part of an hour on a full model, so this
 *  is the user's only window onto it. It is built to read like the run log the
 *  same calculation prints in the terminal — fixed-width counters, one line per
 *  step, a filling bar per step — rather than a generic spinner.
 *
 *  Two rules keep it calm while everything behind it is moving:
 *
 *  * the rows that matter (the checks you will inspect afterwards) are listed
 *    from the first frame and never move — only their state changes;
 *  * the preparation steps churn, so exactly one is shown as active and the
 *    finished ones fold into a collapsed history.
 *
 *  It stays mounted after the run finishes, collapsed, so the panel does not
 *  rearrange itself under the user at the moment the results land. */

/** Bars and markers. Bright while something is happening, settled once it is
 *  done, so "running" and "finished" never read the same at a glance. Exported
 *  because the worst-table coverage line uses the same three, and progress has
 *  to read the same way everywhere in the capacity panel. */
export const ACCENT = "#3DFF7A";
export const ACCENT_DIM = "#16A34A";
export const TRACK = "#1F2937";
const IDLE = "#4B5563";

/** "42s", "4m 02s", "1h 04m" — the same shape the terminal reporter prints. */
export function formatRunElapsed(seconds: number | undefined): string {
  if (!seconds || seconds < 0) return "0s";
  const total = Math.round(seconds);
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return `${minutes}m ${String(rest).padStart(2, "0")}s`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

export type RowState = "pending" | "active" | "done";

/** How one check reads: its state and the figure on the right.
 *
 *  A finished check says how many cases it loaded and how long it took — it is
 *  usually done long before the run as a whole (the girder check is several
 *  times quicker than the stiffened-panel one), and that is exactly when the
 *  user can start inspecting it. */
export function runRow(run: CapacityCalcRunProgress): {
  state: RowState;
  right: string;
  pct: number | null;
} {
  const done = run.cases_ready.length;
  const started = run.started !== false;
  if (run.complete) {
    return {
      state: "done",
      right: `${run.cases_total} cases loaded · ${formatRunElapsed(run.elapsed_s)}`,
      pct: 100,
    };
  }
  if (!started) return { state: "pending", right: "queued", pct: null };
  return {
    state: "active",
    right: `${done} / ${run.cases_total}`,
    pct: Math.round((done / Math.max(run.cases_total, 1)) * 100),
  };
}

/** How one preparation step reads while it is running. ``total`` of 0 means the
 *  step cannot count its work, so it goes indeterminate rather than showing a
 *  percentage it would have to invent. */
export function stepRow(step: CapacityCalcStep): { right: string; pct: number | null } {
  if (!step.total) return { right: "working", pct: null };
  const pct = Math.round(((step.completed ?? 0) / step.total) * 100);
  return { right: `${pct}%`, pct };
}

const MARKER: Record<RowState, string> = {
  done: "✓",
  active: "▸",
  pending: "·",
};

/** One line of the run: a marker, a label, a right-aligned figure, and a bar.
 *
 *  The bar is always rendered, even at zero, so rows keep their height as they
 *  change state and the list never jumps. */
const StatusRow: React.FC<{
  label: string;
  right: string;
  /** null runs the bar indeterminate — the step has no count to report. */
  pct: number | null;
  state: RowState;
  /** Nesting depth of a preparation step; children are indented onto the rail. */
  indent?: number;
  /** Sweep the label. Reserved for the one step that is working right now —
   *  two shimmering lines at once is noise, and the checks below have a moving
   *  bar to say they are alive. */
  shimmer?: boolean;
  title?: string;
}> = ({ label, right, pct, state, indent = 0, shimmer = false, title }) => {
  const fill = state === "done" ? ACCENT_DIM : state === "active" ? ACCENT : IDLE;
  // An active step with no count gets the travelling sliver, which sizes itself
  // in CSS — an inline width would override it.
  const indeterminate = state === "active" && pct === null;
  const width =
    state === "done" ? "100%" : state === "pending" ? "0%" : `${Math.max(pct ?? 0, 1.5)}%`;
  return (
    <div
      className="space-y-1 py-0.5"
      style={{ paddingLeft: indent ? indent * 10 : undefined }}
    >
      <div className="flex items-baseline justify-between gap-2 min-w-0">
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span
            className="shrink-0 text-[10px] leading-none"
            style={{
              color: fill,
              textShadow: state === "active" ? `0 0 6px ${ACCENT}80` : undefined,
            }}
            aria-hidden
          >
            {MARKER[state]}
          </span>
          <span
            className={
              "truncate text-[11px] " +
              (state === "done"
                ? "text-gray-400"
                : state === "active"
                  ? "text-gray-100" + (shimmer ? " ada-shimmer-text" : "")
                  : "text-gray-500")
            }
            title={title ?? label}
          >
            {label}
          </span>
        </span>
        <span className="shrink-0 font-mono text-[11px] tabular-nums text-gray-500">
          {right}
        </span>
      </div>
      <div
        className="h-[3px] overflow-hidden rounded-full"
        style={{ backgroundColor: TRACK }}
      >
        <div
          className={
            "h-full " +
            (indeterminate
              ? "ada-bar-indeterminate"
              : "transition-[width] duration-500 ease-out")
          }
          style={{
            width: indeterminate ? undefined : width,
            backgroundColor: fill,
          }}
        />
      </div>
    </div>
  );
};

/** Small uppercase heading separating the checks from the preparation steps. */
const Eyebrow: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <div className="pb-0.5 text-[10px] uppercase tracking-[0.16em] text-gray-500">
    {children}
  </div>
);

const CapacityRunStatus: React.FC<{ progress: CapacityCalcProgress }> = ({
  progress,
}) => {
  // Collapsed by default once the run is over: at that point the results are
  // what the user came for. Their own toggle wins from then on.
  const [openOverride, setOpenOverride] = useState<boolean | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const open = openOverride ?? !progress.complete;

  const known = progress.cases_known !== false && progress.cases_total > 0;
  const pct = known
    ? Math.round((progress.cases_done / Math.max(progress.cases_total, 1)) * 100)
    : null;

  const steps = progress.prep?.steps ?? [];
  const active = steps.filter((s) => !s.done);
  const finished = steps.filter((s) => s.done);
  const elapsed = formatRunElapsed(progress.elapsed_s);

  const status = progress.complete
    ? { text: "Complete", color: ACCENT_DIM }
    : known
      ? { text: "Running", color: ACCENT }
      : { text: "Starting", color: ACCENT };

  return (
    <section className="border-y border-gray-700 bg-gray-950/60">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left focus-visible:outline focus-visible:outline-1 focus-visible:outline-gray-500"
        onClick={() => setOpenOverride(!open)}
        aria-expanded={open}
      >
        <span
          className={"text-[10px] text-gray-500 transition-transform " + (open ? "rotate-90" : "")}
          aria-hidden
        >
          ▶
        </span>
        <span className="text-[11px] font-semibold uppercase tracking-[0.16em] text-gray-300">
          Code check run
        </span>
        <span className="flex items-center gap-1.5">
          <span
            className={
              "inline-block h-1.5 w-1.5 rounded-full " +
              (progress.complete ? "" : "animate-pulse")
            }
            style={{
              backgroundColor: status.color,
              boxShadow: progress.complete ? undefined : `0 0 6px ${ACCENT}`,
            }}
            aria-hidden
          />
          <span className="text-[11px] text-gray-400">{status.text}</span>
        </span>
        <span className="ml-auto font-mono text-[11px] tabular-nums text-gray-500">
          {elapsed}
        </span>
      </button>

      {/* The headline bar stays visible when the section is collapsed —
          collapsing hides the detail, not where the run has got to. */}
      <div className="px-3 pb-2">
        <div className="mb-1 flex items-baseline justify-between gap-2">
          <span className="truncate text-[11px] text-gray-400" title={progress.message}>
            {progress.complete
              ? "All result cases calculated"
              : progress.message || "Calculating results"}
          </span>
          <span className="shrink-0 font-mono text-[11px] tabular-nums text-gray-400">
            {known ? `${progress.cases_done} / ${progress.cases_total} cases` : "…"}
          </span>
        </div>
        <div
          className="h-1.5 overflow-hidden rounded-full"
          style={{ backgroundColor: TRACK }}
        >
          <div
            className={
              "h-full rounded-full " +
              (pct === null
                ? "ada-bar-indeterminate"
                : "transition-[width] duration-500 ease-out")
            }
            style={{
              width: pct === null ? undefined : `${Math.max(pct, 1.5)}%`,
              backgroundColor: progress.complete ? ACCENT_DIM : ACCENT,
              boxShadow: progress.complete ? undefined : `0 0 8px ${ACCENT}66`,
            }}
          />
        </div>
      </div>

      {open && (
        <div className="space-y-2 px-3 pb-3">
          {/* The checks themselves: listed from the first frame, in run order,
              so nothing appears out of nowhere and nothing moves. */}
          {progress.runs.length > 0 && (
          <div className="space-y-0.5">
            <Eyebrow>Checks</Eyebrow>
            {progress.runs.map((r) => {
              const row = runRow(r);
              return (
                <StatusRow
                  key={r.id}
                  label={r.label ?? r.scope}
                  state={row.state}
                  pct={row.pct}
                  right={row.right}
                />
              );
            })}
          </div>
          )}

          {/* Preparation: reading the SIN, rebuilding models, recovering stress
              fields. Minutes of the wait before the first case lands. */}
          {(active.length > 0 || finished.length > 0) && (
            <div className="space-y-0.5">
              <Eyebrow>Activity</Eyebrow>
              {active.map((step) => (
                <StatusRow
                  key={`${step.label}-${step.depth ?? 0}`}
                  label={step.label}
                  state="active"
                  indent={step.depth ?? 0}
                  shimmer
                  {...stepRow(step)}
                />
              ))}
              {finished.length > 0 && (
                <div className="pt-1">
                  <button
                    type="button"
                    className="flex w-full items-center gap-1.5 text-[11px] text-gray-500 hover:text-gray-300 focus-visible:outline focus-visible:outline-1 focus-visible:outline-gray-600"
                    onClick={() => setHistoryOpen((v) => !v)}
                    aria-expanded={historyOpen}
                  >
                    <span
                      className={
                        "text-[8px] transition-transform " + (historyOpen ? "rotate-90" : "")
                      }
                      aria-hidden
                    >
                      ▶
                    </span>
                    Completed ({finished.length})
                  </button>
                  {historyOpen && (
                    <div className="mt-1 space-y-0.5">
                      {finished.map((step, i) => (
                        <StatusRow
                          key={`${step.label}-${i}`}
                          label={step.label}
                          state="done"
                          indent={step.depth ?? 0}
                          pct={100}
                          right={formatRunElapsed(step.elapsed_s)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <div className="text-[11px] text-gray-500">
            {progress.complete
              ? "Every result case is loaded."
              : "Results appear as each load case finishes."}
          </div>
        </div>
      )}
    </section>
  );
};

export default CapacityRunStatus;
