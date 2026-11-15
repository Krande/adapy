// What Settings > Local jobs shows and sends, as plain functions node --test can import: the
// component (LocalJobsOptions.tsx) renders and keeps the input drafts, useLocalJobs.ts fetches
// and saves.

import type { LocalJobsSettings, LocalJobsView } from "@/services/api/localJobs";

/** The CPU budget field's text for a view: what was set, or empty for every core. */
export function budgetText(view: LocalJobsView): string {
  return view.cpu_budget ?? "";
}

/** "2 running, 3 queued" -- the queue's state in one line. */
export function queueSummary(view: LocalJobsView): string {
  const parts = [`${view.running.length} running`];
  if (view.queued.length) parts.push(`${view.queued.length} queued`);
  return parts.join(", ");
}

/** "2.5 of 8 cores": the budget in force, with a decimal only when it has one. */
export function budgetCoresText(view: LocalJobsView): string {
  const cores = view.cpu_budget_cores;
  return `${cores.toFixed(cores % 1 ? 1 : 0)} of ${view.host_cores} cores`;
}

/**
 * The PUT body for the two input drafts. An empty field is `null`, which returns that limit to
 * its default; anything else is sent as typed, and the server says what is wrong with it.
 */
export function settingsFromDrafts(maxDraft: string, budgetDraft: string): LocalJobsSettings {
  const max = maxDraft.trim();
  const budget = budgetDraft.trim();
  return {
    max_parallel: max === "" ? null : Number(max),
    cpu_budget: budget === "" ? null : budget,
  };
}
