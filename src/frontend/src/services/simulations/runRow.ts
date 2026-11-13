// What a run's one line in the runs list says, and how long it took: no React, so it is tested alone.

import type { SimulationProviderInfo, SimulationRun } from "@/services/api/simulations";

const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");

/** What a run is called on its line: the label it was started with (or its summary carries), else
 *  its provider's title. */
export function runLabel(run: Pick<SimulationRun, "parameters" | "summary" | "provider">, provider: Pick<SimulationProviderInfo, "title"> | null): string {
  return text(run.parameters?.label) || text(run.summary?.label) || provider?.title || run.provider.id;
}

/** The one-line outcome a finished run's summary carries, when it has one. */
export function runOutcome(run: Pick<SimulationRun, "summary">): string {
  return text(run.summary?.status);
}

/** "4 min 12 s" between two moments; empty when either is missing or they are out of order. */
export function durationLabel(from: string | null, to: string | null): string {
  if (!from || !to) return "";
  const seconds = Math.round((Date.parse(to) - Date.parse(from)) / 1000);
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** When a run was started, short enough for its line: the time today, the date and time before. */
export function startedLabel(createdAt: string, now: Date = new Date()): string {
  const at = new Date(createdAt);
  if (Number.isNaN(at.getTime())) return "";
  const time = at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return at.toDateString() === now.toDateString() ? time : `${at.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}
